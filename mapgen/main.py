import json
import multiprocessing
import os
import uuid

import flask


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
from .generate_map import generate


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
    overviewBounds = String(required = False, missing = None)


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


@app.post('/getMap')
@api_input(MapRequestSchema, location = 'form')
def get_map(data):
    req_id = uuid.uuid4().hex
    _global_session[req_id] = data

    script_dir = os.path.dirname(__file__)
    upload_dir = os.path.join(script_dir, "cache")
    os.makedirs(upload_dir, exist_ok = True)

    filename = _process_file(flask.request, 'imgFile', upload_dir)
    if filename:
        # User is trying to upload *something*. Deal with it.
        img_type = data['imgType']
        if img_type == 'j':
            _process_file(flask.request, 'worldFile', upload_dir)
        data['hillshade_file'] = os.path.join(upload_dir, filename)
        _global_session[req_id] = data

    req = {
        'cmd': 'generate',
        'data': req_id
    }

    mp = multiprocessing.get_context('spawn')
    mp.Process(target=generate, args=(req_id, )).start()

    return req_id


@app.get('/checkstatus/<req_id>')
def check_status(req_id):
    try:
        data_dict = _global_session[req_id]
    except KeyError:
        abort(404)

    stat = data_dict.get('gen_status', "Initalizing...")
    if data_dict.get('map_file') is None:
        return {'status': stat, 'done': False}
    else:
        return {'status': 'complete', 'done': True}


@app.get('/getMap/<req_id>')
def get_map_image(req_id):
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
