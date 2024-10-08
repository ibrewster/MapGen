import logging
import json
import multiprocessing
import os
import threading
import uuid

from urllib.parse import unquote

import flask
import ujson

from werkzeug.utils import secure_filename

from . import app, sockets, _global_session, utils
from .mapgenerator import MapGenerator
from .targets import (
    List,
    Value,
    File,
    BaseSchema,
    api_input
)


@app.get('/')
def index():
    try:
        with utils.MySQLCursor() as cursor:
            cursor.execute('SELECT volcano FROM tbllistvolc WHERE HistoricalCat=1')
            volcs = cursor.fetchall()
        volcs = ujson.dumps([x[0] for x in volcs])
    except Exception as e:
        app.logger.warning("Unable to fetch active volcanoes from geodiva", e)
        volcs = ujson.dumps([])

    sta_symbols = MapGenerator.station_symbols
    symbol_img = MapGenerator.icon_images
    staTypes = []

    icon_urls = {}
    icon_symbols = {}

    # Make sure all the "standard" icons are in the icon list, even if we are not using them currently
    for symbol, image in symbol_img.items():
        url = f"static/img/{image}"
        icon_urls[url] = None
        icon_symbols[symbol] = None

    for sta_name, sta_info in sta_symbols.items():
        sta_dict = {}
        sta_dict['name'] = sta_name
        symbol = sta_info['symbol']
        if symbol == "tV":
            continue

        if symbol.startswith('k'):
            url = symbol[1:-4] + "svg"
            symbol = symbol[1:-1]
        else:
            url = symbol_img[symbol]

        sta_dict['symbol'] = symbol
        icon_symbols[symbol] = None

        url = f"static/img/{url}"
        icon_urls[url] = None
        sta_dict['url'] = url
        sta_dict['color'] = sta_info.get('color', '#FFFFFF')
        staTypes.append(sta_dict)

    iconOpts = [{
        'symbol': symbol,
        'url': url,
    }
        for symbol, url
        in zip(icon_symbols.keys(), icon_urls.keys())
    ]

    return flask.render_template("index.html", activevolcs = volcs,
                                 staTypes = staTypes, icons = iconOpts)


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
    overviewColormap = Value(str, default = None)
    showCMTitle = Value(bool, default = False)
    dataTrans = Value(int, default = 0)
    fillOcean = Value(bool, default = False)
    mapFrame = Value(str, default = "fancy")
    tickLabels = List(int, default = [])
    showGrid = Value(bool, default = False)
    showVolcNames = Value(str, default = "")
    showVolcColor = Value(bool, default = False)
    showStationNames = Value(str, default = "")
    staOpt_Name = List(str, default = [])
    staOpt_Icon = List(str, default = [])
    staOpt_Color = List(str, default = [])
    staOpt_Label = List(str, default = [])
    legendTextColor = Value(str, default = '#000000')
    legendBkgColor = Value(str, default = '#FFFFFF')
    legendTextSize = Value(int, default = 12)
    legendBkgTransp = Value(int, default = 100)


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

    mp = multiprocessing.get_context('spawn')
    logging.info("Initalizing generator process")
    mp.Process(target = generator.generate,
               args = (write_queue, req_id),
               daemon = True).start()
    logging.info("Generator started")
    return req_id


@app.get('/getMap')
def get_map_image():
    logging.info("Final image requested")
    req_id = flask.request.args.get('REQ_ID')
    if req_id is None:
        req_id = flask.session.get('REQ_ID')

    if req_id is None:
        flask.abort(404)

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
    response.set_cookie('DownloadComplete', "1")

    return response


@app.get('/checkstatus')
def check_status():
    req_id = flask.session.get('REQ_ID')
    try:
        data_dict = _global_session[req_id]
    except KeyError:
        flask.abort(404)

    stat = data_dict.get('gen_status', "Initalizing...")
    if stat == "FAILED":
        flask.abort(500, 'Unable to generate map. An internal server error occured.')

    if data_dict.get('map_file') is None:
        return {'status': stat, 'done': False}
    else:
        return {'status': 'complete', 'done': True}


socket_queues = {}
ws_objects = {}


# This is weird (to me) but to be able to handle this URL both with and without
# a trailing slash, I have to declare both options as completly seperate functions,
# each calling the same third, undecorated function. Otherwise things get confused.
@sockets.route('/monitor')
def monitor_wo_slash(ws):
    return monitor_socket(ws)


@sockets.route('/monitor/')
def monitor_w_slash(ws):
    return monitor_socket(ws)


def monitor_socket(ws):
    logging.info("New web socket connection opened")
    socket_id = uuid.uuid4().hex
    read_pipe, write_pipe = multiprocessing.Pipe()
    socket_queues[socket_id] = (read_pipe, write_pipe)
    ws_objects[socket_id] = ws
    msg = {'type': 'socketID', 'content': socket_id, }
    ws.send(json.dumps(msg))

    # This thread monitors the sockets, above, and sends information to the client,
    # while the loop below keeps the web socket alive and responds to messages received
    # FROM the client.
    logging.info("Creating webSocket monitor thread")
    thread = threading.Thread(target = _run_monitor_socket,
                              args = (ws, read_pipe))
    thread.start()
    logging.info("Web socket monitor thread started")

    while True:
        msg = ws.receive()
        if msg == "PING":
            ws.send('PONG')

    logging.info("Web socket closed")


def _run_monitor_socket(ws, pipe):
    # Needs to be run in a seperate thread so it doesn't block other requests
    logging.info("Web socket handler thread started")
    while ws.connected:
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

