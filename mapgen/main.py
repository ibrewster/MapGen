from shapely.geometry import Polygon
import shapely_geojson

import json
import os
import tempfile
import uuid
import zipfile

import flask
import osgeo.gdal
import requests

from io import BytesIO
from urllib.parse import unquote, quote

from apiflask import Schema, input as api_input
from apiflask.fields import (
    Float,
    String,
    Boolean,
    Raw,
    List,
)
from apiflask.validators import OneOf
from werkzeug.utils import secure_filename

from . import app


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
    bounds = String(required = True)
    unit = String(required = True, validate = OneOf(['p', 'i', 'c']))
    overview = Boolean(required = False, missing = False)
    overviewWidth = Float()
    imgType = String()
    imgProj = String(required = False, missing = None)
    imgFile = Raw(type = "file", required = False, missing = None)
    station = List(JSON)


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
        filename = secure_filename(file.filename)
        file.save(os.path.join(temp_dir.name, filename))
        return (temp_dir, filename)

    else:
        return (None, None)


def _download_elevation(bounds, temp_dir):
    poly = Polygon.from_bounds(*bounds)
    geojson = quote(shapely_geojson.dumps(poly))
    ids = 151  # DSM hillshade
    url = f'https://elevation.alaska.gov/download?geojson={geojson}&ids={ids}'
    print("Downloading hillshade files")
    tempdir = temp_dir.name
    req = requests.get(url, stream=True)
    if req.status_code != 200:
        print(req.status_code)
        print(req.text)
        return "Error!"
    zf_path = os.path.join(tempdir, 'custom_download.zip')
    with open(zf_path, 'wb') as zf:
        for chunk in req.iter_content(chunk_size=8192):
            if chunk:
                zf.write(chunk)

    # Pull out the various tiff files needed
    tiff_dir = os.path.join(tempdir, 'tiffs')
    os.makedirs(tiff_dir, exist_ok=True)
    print("Extracting tiffs")
    with zipfile.ZipFile(zf_path, 'r') as zf:
        for file in zf.namelist():
            if file.endswith('.zip'):
                print(f"Reading {file}")
                zf_data = BytesIO(zf.read(file))
                with zipfile.ZipFile(zf_data, 'r') as zf2:
                    for tiffile in zf2.namelist():
                        if tiffile.endswith('.tif'):
                            print(f"Extracting {tiffile}")
                            zf2.extract(tiffile, path=tiff_dir)
    return tiff_dir


@app.post('/getMap')
@api_input(MapRequestSchema, location = 'form')
def get_map(data):
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

    warp_bounds = [
        gmt_bounds[0],  # min x
        gmt_bounds[2],  # min y
        gmt_bounds[1],  # max x
        gmt_bounds[3]  # max y
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
    hillshade_file = "great_sitkin.tiff"
    tmp_dir, filename = _process_file(flask.request, 'imgFile')
    if tmp_dir and filename:
        # User is trying to upload *something*. Deal with it.
        img_type = data['imgType']
        if img_type == 't':
            # We can use geotiff files directly, no further work needed
            hillshade_file = os.path.join(tmp_dir.name, filename)
        elif img_type == 'j':
            osgeo.gdal.AllRegister()  # Why? WHY!?!? But needed...
            # Image/World files need to be combined.
            proj = data['imgProj']
            # Should dump the world file to the same directory as the jpeg file
            _process_file(flask.request, 'worldFile', tmp_dir)
            out_file = os.path.join(tmp_dir.name, "hillshade.tiff")
            in_file = os.path.join(tmp_dir.name, filename)
            osgeo.gdal.Warp(out_file, in_file,
                            srcSRS=proj, dstSRS='EPSG:4326',
                            outputBounds=warp_bounds,
                            multithread=True)
            hillshade_file = out_file
    else:
        osgeo.gdal.AllRegister()  # Why? WHY!?!? But needed...
        tmp_dir = tempfile.TemporaryDirectory()
        tiff_dir = _download_elevation(warp_bounds, tmp_dir)

        proj = 'EPSG:3338'  # Alaska Albers
        out_file = os.path.join(tiff_dir, 'hillshade.tiff')
        in_files = [os.path.join(tiff_dir, f) for f in os.listdir(tiff_dir)]
        print("Generating composite hillshade file")

        osgeo.gdal.Warp(out_file, in_files, dstSRS='EPSG:4326',
                        outputBounds=warp_bounds, multithread=True)
        hillshade_file = out_file

    fig.grdimage(hillshade_file, cmap='geo',
                 dpi=300, shading=True, monochrome=True)
    fig.coast(rivers='r/2p,#CBE7FF', water="#CBE7FF", resolution="f")

    main_dir = os.path.dirname(__file__)
    for station in data['station']:
        icon_url = station['icon']
        icon_name = os.path.basename(icon_url)
        icon_path = os.path.join(main_dir, 'static/img', icon_name)
        if not os.path.isfile(icon_path):
            req = requests.get(icon_url)
            if req.status_code != 200:
                continue  # Can't get an icon for this station, move on.
            with open(icon_path, 'wb') as icon_file:
                icon_file.write(req.content)

        position = f"g{station['lon']}/{station['lat']}+w16p"
        fig.image(icon_path, position = position)

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
        with fig.inset(position=pos, box="+gwhite+p1p"):
            fig.coast(
                region=ak_bounds,
                projection="M?",
                water="#CBE7FF",
                land="lightgreen",
                resolution="l",
                shorelines=True,
                # area_thresh = 10000
            )
            x_loc = gmt_bounds[0] + (gmt_bounds[1] - gmt_bounds[0]) / 2
            y_loc = gmt_bounds[2] + (gmt_bounds[3] - gmt_bounds[2]) / 2
            fig.plot(x=[x_loc, ], y=[y_loc, ],
                     style=f"a{star_size}", color="blue")

    save_file = f'{uuid.uuid4().hex}.pdf'
    file_path = os.path.join('/tmp', save_file)
    fig.savefig(file_path, dpi=700)

    with open(file_path, 'rb') as file:
        file_data = file.read()

    os.remove(file_path)

    response = flask.make_response(file_data)
    response.headers.set('Content-Type', 'application/pdf')
    response.headers.set('Content-Disposition', 'attachment',
                         filename="MapImage.pdf")
    response.set_cookie('DownloadComplete', b"1")
    return response
