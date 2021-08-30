import logging
import json
import multiprocessing
import os
import threading
import uuid

import flask
import gevent

from apiflask import abort
# from apiflask.fields import (
#     Float,
#     String,
#     Raw,
#     List,
# )

from streaming_form_data import StreamingFormDataParser

from werkzeug.utils import secure_filename

from . import app, sockets, _global_session
from .mapgenerator import MapGenerator, init_generator_proc
from .targets import ListTarget, TypedTarget, FileTarget, Bounds, JSON

@app.get('/')
def index():
    return flask.render_template("index.html")


# class JSON(String):
#     def _deserialize(self, value, attr, data, **kwargs):
#         if value:
#             try:
#                 return json.loads(value)
#             except ValueError:
#                 return None
# 
#         return None


# class Bounds(String):
#     """Returns a sw_lng, sw_lat, ne_lng, ne_lat tupple"""
# 
#     def _deserialize(self, value, attr, data, **kwargs):
#         if value:
#             try:
#                 return tuple(map(float, unquote(value).split(',')))
#             except ValueError:
#                 return None
# 
#         return None


# class MapRequestSchema(Schema):
#     width = Float(required = True)
#     height = Float(required = False)
#     bounds = String(required=True)
#     mapZoom = Float(required=True)
#     unit = String(required = True, validate = OneOf(['p', 'i', 'c']))
#     overview = String()
#     overviewWidth = Float()
#     imgType = String()
#     imgProj = String(required = False, missing = None)
#     imgFile = Raw(type = "file", required = False, missing = None)
#     station = List(JSON)
#     legend = String()
#     scale = String()
#     overviewBounds = Bounds(required=False, missing=None)
#     insetBounds = List(Bounds, required=False, missing=[])
#     insetZoom = List(Float, required=False, missing=[])
#     insetLeft = List(Float, required=False, missing=[])
#     insetTop = List(Float, required=False, missing=[])
#     insetWidth = List(Float, required=False, missing=[])
#     insetHeight = List(Float, required=False, missing=[])
#     socketID = String()


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

        
def parseFormData(request, file_dir):
    headers = dict(request.headers)
    parser = StreamingFormDataParser(headers = headers)
    # TODO: define Bounds, JSON types
    fields = {
        'width':{'target':TypedTarget(float)},
        'height':{'target':TypedTarget(float)},
        'bounds':{'target':TypedTarget(str)},
        'mapZoom':{'target':TypedTarget(float)},
        'unit':{'target':TypedTarget(str)},
        'overview':{'target':TypedTarget(str)},
        'overviewWidth':{'target':TypedTarget(float)},
        'imgType':{'target':TypedTarget(str)},
        'imgProj':{'target':TypedTarget(str),
                   'default': None,},
        'station':{'target':ListTarget(JSON),
                   'default': []},
        'legend':{'target':TypedTarget(str)},
        'scale':{'target':TypedTarget(str)},
        'overviewBounds':{'target':TypedTarget(Bounds),
                          'default': None,},
        'insetBounds':{'target':ListTarget(Bounds),
                       'default':[]},
        'insetZoom':{'target':ListTarget(float),
                     'default':[]},
        'insetLeft':{'target':ListTarget(float),
                     'default':[]},
        'insetTop':{'target':ListTarget(float),
                    'default':[]},
        'insetWidth':{'target':ListTarget(float),
                      'default':[]},
        'insetHeight':{'target':ListTarget(float),
                       'default':[]},
        'socketID':{'target':TypedTarget(str)},
        
        'imgFile': {'target': FileTarget(file_dir),
                    'default': None},
        'worldFile': {'target': FileTarget(file_dir),
                    'default': None},        
    }
    
    for field, target_def in fields.items():
        target = target_def['target']
        parser.register(field, target)
    
    parser.register('imgFile', SingleFileTarget("/tmp/someFile.txt"))
    chunk_size = 4096
    while True:
        chunk = request.stream.read(chunk_size)
        if len(chunk) == 0:
            break
        parser.data_received(chunk)
        gevent.sleep(0)
        
    values = {}
    for field, target_def in fields.items():
        target = target_def['target']
        if not target.finished:
            # See if we have a default value
            try:
                value = target_def['default']
            except KeyError:
                raise ValueError("No value provided, and no default")
        else:
            value = target.value
        
        values[field] = value
        
    return values

@app.post('/getMap')
# @api_input(MapRequestSchema, location = 'form')
def request_map():
    logging.info("Map request received")    
    req_id = uuid.uuid4().hex    
    flask.session['REQ_ID'] = req_id
    generator = MapGenerator()
    upload_dir = generator.tempdir()
    
    try:
        data = parseFormData(flask.request, upload_dir)
    except ValueError:
        abort(400, "Missing parameter")
    
    _global_session[req_id] = data
    generator.setReqId(req_id)
    
    socket_id = data['socketID']
    read_queue, write_queue = socket_queues[socket_id]

    logging.info("Processing upload(s)")
    filename = data['imgFile']
    logging.info("File upload processed")
    if filename:
        # User is trying to upload *something*. Deal with it.
        data['hillshade_file'] = os.path.join(upload_dir, filename)
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
    req_id = flask.session.get('REQ_ID')
    if req_id is None:
        abort(404)

    file_path = _global_session[req_id]['map_file']
    with open(file_path, 'rb') as file:
        file_data = file.read()

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

