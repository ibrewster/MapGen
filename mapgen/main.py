import json
import multiprocessing
import os
import uuid

import flask

from urllib.parse import unquote

from apiflask import abort, Schema, input as api_input
from apiflask.fields import (
    Float,
    String,
    Raw,
    List,
)
from apiflask.validators import OneOf
from werkzeug.utils import secure_filename

from . import app, _global_session
from .mapgenerator import MapGenerator


@app.get('/')
def index():
    return flask.render_template("index.html")


class JSON(String):
    def _deserialize(self, value, attr, data, **kwargs):
        if value:
            try:
                return json.loads(value)
            except ValueError:
                return None

        return None


class Bounds(String):
    """Returns a sw_lng, sw_lat, ne_lng, ne_lat tupple"""

    def _deserialize(self, value, attr, data, **kwargs):
        if value:
            try:
                return tuple(map(float, unquote(value).split(',')))
            except ValueError:
                return None

        return None


class MapRequestSchema(Schema):
    width = Float(required = True)
    height = Float(required = False)
    bounds = String(required=True)
    mapZoom = Float(required=True)
    unit = String(required = True, validate = OneOf(['p', 'i', 'c']))
    overview = String()
    overviewWidth = Float()
    imgType = String()
    imgProj = String(required = False, missing = None)
    imgFile = Raw(type = "file", required = False, missing = None)
    station = List(JSON)
    legend = String()
    scale = String()
    overviewBounds = Bounds(required=False, missing=None)
    insetBounds = List(Bounds, required=False, missing=[])
    insetZoom = List(Float, required=False, missing=[])
    insetLeft = List(Float, required=False, missing=[])
    insetTop = List(Float, required=False, missing=[])
    insetWidth = List(Float, required=False, missing=[])
    insetHeight = List(Float, required=False, missing=[])


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


@app.post('/getMap')
@api_input(MapRequestSchema, location = 'form')
def request_map(data):
    req_id = uuid.uuid4().hex
    flask.session['REQ_ID'] = req_id
    _global_session[req_id] = data
    generator = MapGenerator(req_id)
    upload_dir = generator.tempdir()

    filename = _process_file(flask.request, 'imgFile', upload_dir)

    if filename:
        # User is trying to upload *something*. Deal with it.
        img_type = data['imgType']
        if img_type == 'j':
            _process_file(flask.request, 'worldFile', upload_dir)
        data['hillshade_file'] = os.path.join(upload_dir, filename)
        _global_session[req_id] = data

    def err_callback(error):
        _gen_fail_callback(req_id, error)

    mp = multiprocessing.get_context('spawn')
    pool = mp.Pool(processes = 1)
    pool.apply_async(generator.generate,
                     error_callback = err_callback)
    # mp.Process(target=generator.generate).start()

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
