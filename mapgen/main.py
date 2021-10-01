import logging
import json
import multiprocessing
import os
import threading
import uuid

from urllib.parse import unquote

import flask
import gevent
import ujson

from apiflask import abort
from werkzeug.utils import secure_filename

from . import app, sockets, _global_session
from .mapgenerator import MapGenerator, init_generator_proc
from .targets import (
    List,
    Value,
    File,
    BaseSchema,
    api_input
)


@app.get('/')
def index():
    return flask.render_template("index.html")


def allowed_file(filename):
    ALLOWED_EXTENSIONS = ['jpg', 'jpeg', 'tif', 'tiff', 'jgw', 'tfw']
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _process_file(request, name, save_dir):
    if name in request.files:
        file = request.files[name]
        if file.filename == "":
            return None
        if not allowed_file(file.filename):
            return None

        filename = secure_filename(file.filename)
        file.save(os.path.join(save_dir, filename))
        return filename

    else:
        return None


def _gen_fail_callback(req_id, error):
    print("Map generation failed! Error:")
    print(error)
    print("-->{}<--".format(error.__cause__))
    data = _global_session[req_id]
    data['gen_status'] = "FAILED"
    _global_session[req_id] = data


def Bounds(value):
    """Returns a sw_lng, sw_lat, ne_lng, ne_lat tupple"""
    if value:
        if isinstance(value, bytes):
            value = value.decode('UTF-8')
        try:
            return tuple(map(float, unquote(value).split(',')))
        except ValueError:
            return None

    return None


def JSON(value):
    if value:
        try:
            return ujson.loads(value)
        except ValueError:
            return None

    return None


class MapSchema(BaseSchema):
    width = Value(float)
    height = Value(float)
    bounds = Value(str)
    mapZoom = Value(float)
    unit = Value(str)
    overview = Value(str)
    overviewWidth = Value(float)
    imgType = Value(str)
    imgProj = Value(str, default = None)
    station = List(JSON, default = [])
    legend = Value(str)
    scale = Value(str)
    overviewBounds = Value(Bounds, default = None)
    insetBounds = List(Bounds, default = [])
    insetZoom = List(float, default = [])
    insetLeft = List(float, default = [])
    insetTop = List(float, default = [])
    insetWidth = List(float, default = [])
    insetHeight = List(float, default = [])
    socketID = Value(str)
    imgFile = File(default = None)
    worldFile = File(default = None)
    colorMap = Value(str, default = None)
    cmMin = Value(float, default = None)
    cmMax = Value(float, default = None)
    plotData = File(default = None)
    colorbar = Value(str)
    scaleunits = Value(str, default = None)
    latcol = Value(str, default = None)
    loncol = Value(str, default = None)
    valcol = Value(str, default = None)
    mapColormap = Value(str, default = None)
    showCMTitle = Value(bool, default = False)


@app.post('/getMap')
@api_input(MapSchema)
def request_map(data):
    logging.info("Map request received")
    req_id = uuid.uuid4().hex
    flask.session['REQ_ID'] = req_id
    generator = MapGenerator()
    upload_dir = generator.tempdir()

    _global_session[req_id] = data
    generator.setReqId(req_id)

    socket_id = data['socketID']
    read_queue, write_queue = socket_queues[socket_id]

    logging.info("Processing upload(s)")
    filename = data.get('imgFile').name if data.get('imgFile') else None
    logging.info("File upload processed")
    if filename:
        data['imgFile'].save(upload_dir)
        if data.get('worldFile') and data['worldFile'].name:
            data['worldFile'].save(upload_dir)

        # User is trying to upload *something*. Deal with it.
        data['hillshade_file'] = os.path.join(upload_dir, filename)

    if data.get('plotData') and data['plotData'].name:
        data['plotData'].save(upload_dir)
        data['plotDataFile'] = os.path.join(upload_dir, data['plotData'].name)

    _global_session[req_id] = data

    def err_callback(error):
        write_queue.send('ERROR')
        _gen_fail_callback(req_id, error)

    mp = multiprocessing.get_context('spawn')
    logging.info("Initalizing generator process")
    pool = mp.Pool(processes = 1, initializer = init_generator_proc,
                   initargs = (write_queue, ))
    pool.apply_async(generator.generate,
                     error_callback = err_callback)
    # mp.Process(target=generator.generate).start()
    logging.info("Generator started")
    return req_id


@app.get('/getMap')
def get_map_image():
    logging.info("Final image requested")
    req_id = flask.session.get('REQ_ID')
    if req_id is None:
        abort(404)

    file_path = _global_session[req_id]['map_file']
    with open(file_path, 'rb') as file:
        file_data = file.read()

    logging.info(f"Loaded file with length {len(file_data)}")
    os.remove(file_path)
    del _global_session[req_id]

    response = flask.make_response(file_data)
    response.headers.set('Content-Type', 'application/pdf')
    response.headers.set('Content-Disposition', 'attachment',
                         filename="MapImage.pdf")
    response.set_cookie('DownloadComplete', b"1")

    return response


@app.get('/checkstatus')
def check_status():
    req_id = flask.session.get('REQ_ID')
    try:
        data_dict = _global_session[req_id]
    except KeyError:
        abort(404)

    stat = data_dict.get('gen_status', "Initalizing...")
    if stat == "FAILED":
        abort(500, 'Unable to generate map. An internal server error occured.')

    if data_dict.get('map_file') is None:
        return {'status': stat, 'done': False}
    else:
        return {'status': 'complete', 'done': True}


socket_queues = {}


@sockets.route('/monitor')
def monitor_socket(ws):
    logging.info("New web socket connection opened")
    socket_id = uuid.uuid4().hex
    read_pipe, write_pipe = multiprocessing.Pipe()
    socket_queues[socket_id] = (read_pipe, write_pipe)
    msg = {'type': 'socketID', 'content': socket_id, }
    ws.send(json.dumps(msg))

    logging.info("Creating webSocket monitor thread")
    thread = threading.Thread(target = _run_monitor_socket,
                              args = (ws, read_pipe))
    thread.start()
    logging.info("Web socket monitor thread started")
    while thread.is_alive():
        gevent.spawn(_recieve_ws, ws)
        gevent.sleep(1)

    logging.info("Web socket closed")


def _recieve_ws(ws):
    msg = None
    try:
        with gevent.Timeout(.01):
            msg = ws.receive()
    except:
        pass

    if msg == "PING":
        ws.send('PONG')


def _run_monitor_socket(ws, pipe):
    # Needs to be run in a seperate thread so it doesn't block other requests
    logging.info("Web socket handler thread started")
    while not ws.closed:
        # Check and loop rather than blocking indefinitely
        # so we can know if the socket has closed.
        msg_waiting = pipe.poll(.25)
        if not msg_waiting:
            continue

        message = pipe.recv()
        message = {'type': 'status',
                   'content': message}
        ws.send(json.dumps(message))

    logging.info("Exiting web socket handler thread")

