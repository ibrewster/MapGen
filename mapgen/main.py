import os
import tempfile
import uuid

import flask
import osgeo.gdal

from urllib.parse import unquote

from apiflask import Schema, input as api_input
from apiflask.fields import Float, String, Boolean, Raw
from apiflask.validators import OneOf

from werkzeug.utils import secure_filename

from . import app


@app.get('/')
def index():
    return flask.render_template("index.html")


class MapRequestSchema(Schema):
    width = Float(required = True)
    height = Float(required = False)
    bounds = String(required = True)
    unit = String(required = True, validate = OneOf(['p', 'i', 'c']))
    overview = Boolean(required = False, missing = False)
    overviewWidth = Float()
    imgType = String()
    imgProj = String(required = False, missing = None)
    imgFile = Raw(type = "file", required = False, missing = None)


def allowed_file(filename):
    ALLOWED_EXTENSIONS = ['jpg', 'jpeg', 'tif', 'tiff', 'jgw', 'tfw']
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _process_file(request, name, temp_dir = None):
    if name in request.files:
        file = request.files[name]
        if file.filename == "":
            return (None, None)
        if not allowed_file(file.filename):
            return (None, None)

        if temp_dir is None:
            temp_dir = tempfile.TemporaryDirectory()
            print(temp_dir, temp_dir.name)
        filename = secure_filename(file.filename)
        file.save(os.path.join(temp_dir.name, filename))
        return (temp_dir, filename)

    else:
        return (None, None)


@app.post('/getMap')
@api_input(MapRequestSchema, location = 'form')
def get_map(data):
    print(data)
    width = data['width']
    bounds = data['bounds']
    unit = data['unit']
    overview = data['overview']

    try:
        import pygmt
    except Exception:
        os.environ['GMT_LIBRARY_PATH'] = '/usr/local/lib'
        import pygmt

    sw_lng, sw_lat, ne_lng, ne_lat = unquote(bounds).split(',')
    gmt_bounds = [
        float(sw_lng),
        float(ne_lng),
        float(sw_lat),
        float(ne_lat)
    ]

#     utm_left = gmt_bounds[0]
#     if utm_left < -180:
#         utm_left += 360

#     utm_zone = math.ceil((utm_left + 180) / 6)
#
#     UTMChars = "CDEFGHJKLMNPQRSTUVWXX"
#     utm_lat = gmt_bounds[2]
#     if -80 <= utm_lat <= 84:
#         utm_char = UTMChars[math.floor((utm_lat + 80) / 8)]
#     else:
#         utm_char = UTMChars[-1]

    proj = f"M{width}{unit}"
    # proj = f"U{utm_zone}{utm_char}/{width}{unit}"
    fig = pygmt.Figure()
    fig.basemap(projection=proj, region=gmt_bounds, frame=('WeSn', 'afg'))

    # See if we have a file to deal with for this
    hillshade_file = "alaska_2s.grd"
    tmp_dir, filename = _process_file(flask.request, 'imgFile')
    if tmp_dir and filename:
        # User is trying to upload *something*. Deal with it.
        img_type = data['imgType']
        if img_type == 't':
            # We can use geotiff files directly, no further work needed
            hillshade_file = os.path.join(tmp_dir.name, filename)
        elif img_type == 'j':
            osgeo.gdal.AllRegister()
            # Image/World files need to be combined.
            proj = data['imgProj']
            # Should dump the world file to the same directory as the jpeg file
            _process_file(flask.request, 'worldFile', tmp_dir)
            print("TempDir:", tmp_dir.name)
            out_file = os.path.join(tmp_dir.name, "hillshade.tiff")
            in_file = os.path.join(tmp_dir.name, filename)
            osgeo.gdal.Warp(out_file, in_file,
                            srcSRS = proj, dstSRS = 'EPSG:4326')
            hillshade_file = out_file

    fig.grdimage(hillshade_file, cmap = 'geo',
                 dpi = 300, shading = True, monochrome = True)
    fig.coast(rivers = 'r/2p,#FFFFFF', water = "#00FFFF", resolution = "f")

    if overview:
        ak_bounds = [
            -190.0,
            -147.68,
            48.5,
            69.5
        ]

        inset_width = data['overviewWidth']
        pos = f"jBR+w{inset_width}{unit}+o0.1c"
        star_size = "16p"
        with fig.inset(position = pos, box = "+gwhite+p1p"):
            fig.coast(
                region = ak_bounds,
                projection = "M?",
                water = "#00FFFF",
                land = "lightgreen",
                resolution = "l",
                shorelines = True,
                # area_thresh = 10000
            )
            x_loc = gmt_bounds[0] + (gmt_bounds[1] - gmt_bounds[0]) / 2
            y_loc = gmt_bounds[2] + (gmt_bounds[3] - gmt_bounds[2]) / 2
            fig.plot(x = [x_loc, ], y = [y_loc, ],
                     style = f"a{star_size}", color = "blue")

    save_file = f'{uuid.uuid4().hex}.pdf'
    file_path = os.path.join('/tmp', save_file)
    fig.savefig(file_path, dpi = 700)

    with open(file_path, 'rb') as file:
        file_data = file.read()

    os.remove(file_path)

    response = flask.make_response(file_data)
    response.headers.set('Content-Type', 'application/pdf')
    response.headers.set('Content-Disposition', 'attachment',
                         filename = "MapImage.pdf")
    response.set_cookie('DownloadComplete', b"1")

    return response
