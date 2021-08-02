from shapely.geometry import Polygon
import shapely_geojson

import logging
import math
import multiprocessing
import os
import pickle
import socket
import signal
import tempfile
import uuid
import zipfile

from io import BytesIO

import osgeo.gdal
import requests
import vincenty

from urllib.parse import unquote, quote
from file_cache import FileCache

_global_session = FileCache()

def run_process(queue):
    logging.info("Starting map generator process")
    print("Starting map generator process")
    original_sigint_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        with multiprocessing.Pool() as pool:
            signal.signal(signal.SIGINT, original_sigint_handler)
            while True:
                try:
                    (client, addr) = queue.accept()
                except KeyboardInterrupt:
                    return

                msg_len = b""
                while len(msg_len) < 4:
                    msg_len += client.recv(4 - len(msg_len))

                msg_len = int(msg_len)
                msg = b''
                while len(msg) < msg_len:
                    msg += client.recv(msg_len - len(msg))

                if msg == b'':
                    continue  # No data

                print("Message received:", msg)
                msg = pickle.loads(msg)
                if not isinstance(msg, dict):
                    logging.warning(f"Unknown message received: {msg}")
                    continue
                if msg.get('cmd') == "generate":
                    pool.apply_async(generate, (msg.get('data'), ))
    except KeyboardInterrupt:
        return

def _download_elevation(bounds, temp_dir, req_id):
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
    data = _global_session[req_id]
    data['gen_status'] = "Processing hillshade data..."
    _global_session[req_id] = data

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


def generate(req_id):
    data = _global_session.get(req_id)
    width = data['width']
    bounds = data['bounds']
    unit = data['unit']
    overview = data['overview']
    if overview == "False":
        overview = False

    station_symbols = {
        'gps.png': {'symbol': 'a16p',
                    'color': 'red', },
        'seismometer.png': {'symbol': 't16p',
                            'color': 'green', },
        'tiltmeter.png': {'symbol': 'ktiltmeter.eps/16p',
                          'color': 'blue', },
        'webcam.png': {'symbol': 'kwebcam.eps/16p',
                       'color': 'blue',  # Really, could be anything...
                       },
    }

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

    basemap_args = {
        'projection': proj,
        'region': gmt_bounds,
        'frame': ('WeSn', 'afg'),
    }

    fig.basemap(**basemap_args)

    # See if we have a file to deal with for this
    hillshade_file = data.get('hillshade_file')
    if hillshade_file:
        # See if we need to process this
        img_type = data['imgType']
        if img_type == 'j':
            osgeo.gdal.AllRegister()  # Why? WHY!?!? But needed...
            # Image/World files need to be combined.
            proj = data['imgProj']
            out_dir = os.path.dirname(hillshade_file)
            out_file = os.path.join(out_dir, "hillshade.tiff")

            data['gen_status'] = "Processing uploads..."
            _global_session[req_id] = data

            osgeo.gdal.Warp(out_file, hillshade_file,
                            srcSRS=proj, dstSRS='EPSG:4326',
                            outputBounds=warp_bounds,
                            multithread=True)

            world_file = os.path.basename(hillshade_file)
            extension = world_file[world_file.index('.') + 1:]
            wf_ext = f'{extension[0]}{extension[-1]}w'
            wf_name = world_file[:world_file.index('.')]
            world_file = f'{wf_name}.{wf_ext}'
            try:
                os.remove(os.path.join(os.path.dirname(hillshade_file),
                                       world_file)
                          )
            except FileNotFoundError:
                print("Unable to remove world fie")
                print(world_file)

            # Done with the uploaded file (if any), delete it
            try:
                os.remove(hillshade_file)
            except FileNotFoundError:
                print("Unable to remove upload")

            hillshade_file = out_file
    else:
        osgeo.gdal.AllRegister()  # Why? WHY!?!? But needed...
        tmp_dir = tempfile.TemporaryDirectory()
        data['gen_status'] = "Downloading hillshade files..."
        _global_session[req_id] = data
        tiff_dir = _download_elevation(warp_bounds, tmp_dir, req_id)

        proj = 'EPSG:3338'  # Alaska Albers
        out_file = os.path.join(tiff_dir, 'hillshade.tiff')
        in_files = [os.path.join(tiff_dir, f) for f in os.listdir(tiff_dir)]
        print("Generating composite hillshade file")

        osgeo.gdal.Warp(out_file, in_files, dstSRS='EPSG:4326',
                        outputBounds=warp_bounds, multithread=True)
        hillshade_file = out_file

    data['gen_status'] = "Drawing map image..."
    _global_session[req_id] = data
    fig.grdimage(hillshade_file, cmap='geo',
                 dpi=300, shading=True, monochrome=True)

    # Done with the uploaded file (if any), delete it
    try:
        os.remove(hillshade_file)
    except FileNotFoundError:
        print("Unable to remove upload")

    data['gen_status'] = "Drawing coastlines..."
    _global_session[req_id] = data
    fig.coast(rivers='r/2p,#CBE7FF', water="#CBE7FF", resolution="f")

    if data['scale'] != 'False':
        data['gen_status'] = "Adding Scale Bar..."
        _global_session[req_id] = data
        # figure out middle latitude for map
        mid_lat = gmt_bounds[2] + ((gmt_bounds[3] - gmt_bounds[2]) / 2)
        map_width = vincenty.vincenty((mid_lat, gmt_bounds[0]),
                                      (mid_lat, gmt_bounds[1]))
        scale_length = math.ceil(map_width / 8)
        offset = .65
        if data['scale'][0] == 'T':
            offset += .3

        offset = str(offset) + "c"

        if data['scale'][1] in ['L', 'R']:
            offset = '.375c/' + offset

        map_scale = f'j{data["scale"]}+w{scale_length}k+f+o{offset}+c{mid_lat}N+l'
        fig.basemap(map_scale = map_scale, F = '+gwhite+p')

    print("Plotting stations")
    data['gen_status'] = "Plotting Stations..."
    _global_session[req_id] = data

    main_dir = os.path.dirname(__file__)
    img_dir = os.path.join(main_dir, 'static/img')
    cur_dir = os.getcwd()
    os.chdir(img_dir)
    used_symbols = {}
    for station in data['station']:
        icon_url = station['icon']
        icon_name = os.path.basename(icon_url)
        sta_x = station['lon']
        sta_y = station['lat']

        symbol = station_symbols.get(icon_name, {}).get('symbol')
        color = station_symbols.get(icon_name, {}).get('color')

        if symbol is not None:
            used_symbols[icon_name] = station_symbols.get(icon_name)
            fig.plot(x=[sta_x, ], y=[sta_y, ],
                     style=symbol, color=color)
        else:
            icon_path = os.path.join(main_dir, 'static/img', icon_name)
            used_symbols[icon_name] = icon_path

            if not os.path.isfile(icon_path):
                req = requests.get(icon_url)
                if req.status_code != 200:
                    continue  # Can't get an icon for this station, move on.
                with open(icon_path, 'wb') as icon_file:
                    icon_file.write(req.content)

            position = f"g{sta_x}/{sta_y}+w16p"
            fig.image(icon_path, position = position)

    legend = data['legend']
    if legend != "False":
        print("Adding legend")
        # data['gen_status'] = "Adding Legend..."
        # _global_session[req_id] = data
        with tempfile.NamedTemporaryFile('w+') as file:
            for idx, (name, symbol) in enumerate(used_symbols.items()):
                sym_label = name[:-4]
#                 if isinstance(symbol, str):
#                     file.write('G 8p\n')
#                     file.write(f'I {symbol} 16p LM\n')
#                     file.write('G -1l\n')
#                     # file.write('G -4p\n')
#                     file.write('P .38 - - - - - - -\n')
#                     file.write(f'T {sym_label}\n')
#                     file.write('G 1l\n')
#                     file.write('G -5p\n')
#                     continue

                sym_char = symbol['symbol'][0]  # First character
                if sym_char == 'k':
                    try:
                        end_idx = symbol['symbol'].index('/')
                        sym_char = symbol['symbol'][:end_idx]
                    except ValueError:
                        pass

                sym_color = symbol['color']
                file.write(f'S 11p {sym_char} 16p {sym_color} - 23p {sym_label}')
                file.write('\n')

            file.seek(0)
            file_name = file.name
            fig.legend(
                file_name,
                position = f"J{legend}+j{legend}+o0.2c+l1.5",
                box="+gwhite+p1p"
            )

    os.chdir(cur_dir)
    print("Adding Overview")
    data['gen_status'] = "Adding Overview Map..."
    _global_session[req_id] = data
    if overview:
        ak_bounds = [
            -190.0, -147.68, 48.5, 69.5
        ]

        if data['overviewBounds']:
            (
                sw_lng,
                sw_lat,
                ne_lng,
                ne_lat
            ) = unquote(data['overviewBounds']).split(',')

            ak_bounds = [
                float(sw_lng),
                float(ne_lng),
                float(sw_lat),
                float(ne_lat)
            ]

        inset_width = data['overviewWidth']
        pos = f"j{overview}+w{inset_width}{unit}+o0.1c"
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

    data['gen_status'] = "Saving final image..."
    _global_session[req_id] = data
    save_file = f'{uuid.uuid4().hex}.pdf'
    script_dir = os.path.dirname(__file__)
    cache_dir = os.path.join(script_dir, "cache")
    os.makedirs(cache_dir, exist_ok = True)
    file_path = os.path.join(cache_dir, save_file)
    fig.savefig(file_path, dpi=700)
    data['map_file'] = file_path
    data['gen_status'] = "Complete"
    _global_session[req_id] = data
    print(file_path)


if __name__ == "__main__":
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    cache_dir = os.path.join(os.path.dirname(__file__), 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    sock_file = os.path.join(cache_dir, "gen_sock.socket")
    try:
        sock.bind(sock_file)
    except OSError:
        os.remove(sock_file)
        sock.bind(sock_file)

    os.chmod(sock_file, 0777)

    sock.listen(5)
    run_process(sock)
    os.unlink(sock_file)
