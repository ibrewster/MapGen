import os
import uuid

import flask

from urllib.parse import unquote

from apiflask import Schema, input as api_input
from apiflask.fields import Float, String, Boolean
from apiflask.validators import OneOf

from . import app


@app.get('/')
def index():
    return flask.render_template("index.html")


class MapRequestSchema(Schema):
    width = Float(required = True)
    height = Float(required = False)
    bounds = String(required = True)
    unit = String(required = True, validate = OneOf(['p', 'i', 'c']))
    overview = Boolean(required = False, missing = True)


@app.get('/getMap')
@api_input(MapRequestSchema, location = 'query')
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
    fig.grdimage("alaska_2s.grd", cmap = 'geo',
                 dpi = 700, shading = True, monochrome = True)
    fig.coast(rivers = 'r/2p,#FFFFFF', water = "#00FFFF", resolution = "f")

    if overview:
        ak_bounds = [
            -190.0,
            -147.68,
            48.5,
            69.5
        ]

        inset_width = width / 4
        pos = f"jBR+w{inset_width}{unit}+o0.1c"
        star_size = inset_width / 12
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
                     style = f"a{star_size}{unit}", color = "blue")

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
