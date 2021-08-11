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

from osgeo import osr

import osgeo.gdal
import requests
import vincenty

from urllib.parse import unquote

try:
    from . import _global_session
except ImportError:
    try:
        from file_cache import FileCache
    except ImportError:
        from .file_cache import FileCache

    _global_session = FileCache()


def _update_status(status, req_id, data = None):
    if data is None:
        data = _global_session[req_id]

    data['gen_status'] = status
    _global_session[req_id] = data


def run_process(queue):
    """
    If running as a standalone script, this function will launch
    the process and create a listening socket for communication.

    You can also simply call the generate function directly, either
    in a process/thread or synchronously if desired.
    """
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


def _get_extents(src, proj=None):
    ulx, xres, xskew, uly, yskew, yres = src.GetGeoTransform()
    lrx = ulx + (src.RasterXSize * xres)
    lry = uly + (src.RasterYSize * yres)

    src_srs = osr.SpatialReference()
    if proj is not None:
        epsg_code = int(proj.replace('EPSG:', ''))
        src_srs.ImportFromEPSG(epsg_code)
    else:
        src_srs.ImportFromWkt(src.GetProjection())

    tgt_srs = src_srs.CloneGeogCS()

    transform = osr.CoordinateTransformation(src_srs, tgt_srs)
    # top-left, top-right,bottom-right,bottom-left
    corners = ((ulx, uly), (lrx, uly), (lrx, lry), (ulx, lry))
    trans_corners = transform.TransformPoints(corners)

    uly, ulx, _ = trans_corners[0]
    ury, urx, _ = trans_corners[1]
    lry, lrx, _ = trans_corners[2]
    lly, llx, _ = trans_corners[3]

    # figure out which X is to the left.
    # Make both upper and lower coordinates
    # negitive for easy comparison
    comp_upper = ulx
    comp_lower = llx

    if comp_upper > 0:
        comp_upper -= 360

    if comp_lower > 0:
        comp_lower -= 360

    if comp_upper < comp_lower:
        minx = ulx
    else:
        minx = llx

    comp_upper = urx
    comp_lower = lrx
    if comp_upper > 0:
        comp_upper -= 360

    if comp_lower > 0:
        comp_lower -= 360

    if comp_upper > comp_lower:
        maxx = urx
    else:
        maxx = lrx

    miny = min(lly, lry)
    maxy = max(uly, ury)

    if minx > maxx:
        minx -= 360

    return [minx, miny, maxx, maxy]


def _download_elevation(bounds, temp_dir, req_id):
    if bounds[0] < -180 or bounds[2] > 180 or bounds[0] > bounds[2]:
        # Crossing dateline. Need to split request.
        bounds2 = bounds.copy()
        bounds3 = bounds.copy()
        # Make bounds be only west of dateline
        if bounds3[0] < 0:
            bounds3[0] += 360
        bounds3[2] = 180

        bounds2[0] = -180
        if bounds2[2] > 0:
            bounds2[2] -= 360

        bounds_list = [bounds3, bounds2]
    else:
        bounds_list = [bounds, ]

    ids = 151  # DSM hillshade
    URL_BASE = 'https://elevation.alaska.gov'
    list_url = f'{URL_BASE}/query.json'
    url = f'{URL_BASE}/download'
    est_size = 0
    print("Downloading hillshade files")
    tempdir = temp_dir.name
    zf_path = os.path.join(tempdir, 'custom_download.zip')
    tiff_dir = os.path.join(tempdir, 'tiffs')
    os.makedirs(tiff_dir, exist_ok=True)

    loaded_bytes = 0
    pc = 0
    chunk_size = 1024 * 1024 * 10  # 10 MB

    for bound in bounds_list:
        poly = Polygon.from_bounds(*bound)
        geojson = shapely_geojson.dumps(poly)

        # get file listings
        req = requests.post(list_url, data = {'geojson': geojson, })

        if req.status_code != 200:
            print("Unable to get file listings")
        else:
            files = req.json()
            print(files)
            try:
                file_info = next((x for x in files if x['project_id'] == ids))
            except StopIteration:
                pass
            else:
                print(file_info)
                est_size += file_info.get('bytes', -1)

        req = requests.get(url,
                           params = {'geojson': geojson,
                                     'ids': ids},
                           stream=True)
        if req.status_code != 200:
            print(req.status_code)
            print(req.text)
            continue

        with open(zf_path, 'wb') as zf:
            for chunk in req.iter_content(chunk_size=chunk_size):
                if chunk:
                    loaded_bytes += zf.write(chunk)
                    if est_size > 0:
                        pc = round((loaded_bytes / est_size) * 100, 1)

                        _update_status({
                            'status': "Downloading hillshade files...",
                            'progress': pc
                        }, req_id
                        )

        print("Downloaded", loaded_bytes, "bytes")

        # Pull out the various tiff files needed
        print("Extracting tiffs")

        _update_status("Decompressing hillshade data...", req_id)

        with zipfile.ZipFile(zf_path, 'r') as zf:
            for file in zf.namelist():
                if file.endswith('.zip'):
                    print(f"Reading {file}")
                    zf_data = BytesIO(zf.read(file))
                    with zipfile.ZipFile(zf_data, 'r') as zf2:
                        for tiffile in zf2.namelist():
                            if tiffile.endswith('.tif'):
                                if os.path.isfile(os.path.join(tiff_dir, tiffile)):
                                    continue  # already extracted, move on
                                print(f"Extracting {tiffile}")
                                zf2.extract(tiffile, path=tiff_dir)
    return tiff_dir


def _process_files(dest_dir, all_files, warp_bounds, req_id, proj = None):
    osgeo.gdal.AllRegister()  # Why? WHY!?!? But needed...
    files = []
    num_files = len(all_files)
    for idx, in_file in enumerate(all_files):
        print("Processing image", idx, "of", len(all_files))

        in_path, in_ext = os.path.splitext(in_file)
        out_file = f"{in_path}-processed.tiff"
        # Considered skipping if already processed, but the bounds might be different
        # if os.path.isfile(out_file):
        # continue  # Already processed. Move on.

        files.append(out_file)

        ds = osgeo.gdal.Open(in_file)
        file_bounds = _get_extents(ds, proj)
        del ds

        use_bounds = False

        # Make signs of warp and file bounds match
        # Stupid dateline!
        if file_bounds[0] < 0 and warp_bounds[0] > 0:
            file_bounds[0] += 360
        if file_bounds[0] > 0 and warp_bounds[0] < 0:
            file_bounds[0] -= 360

        if file_bounds[2] < 0 and warp_bounds[2] > 0:
            file_bounds[2] += 360
        if file_bounds[2] > 0 and warp_bounds[2] < 0:
            file_bounds[2] -= 360

        # limit extents to warp_bounds
        if file_bounds[0] < warp_bounds[0]:
            file_bounds[0] = warp_bounds[0]
            use_bounds = True
        if file_bounds[1] < warp_bounds[1]:
            file_bounds[1] = warp_bounds[1]
            use_bounds = True
        if file_bounds[2] > warp_bounds[2]:
            file_bounds[2] = warp_bounds[2]
            use_bounds = True
        if file_bounds[3] > warp_bounds[3]:
            file_bounds[3] = warp_bounds[3]
            use_bounds = True

        kwargs = {
            "dstSRS": "EPSG:4326",
            "multithread": True,
            "warpOptions": ['NUM_THREADS=ALL_CPUS'],
            "creationOptions": ['NUM_THREADS=ALL_CPUS'],
        }

        if proj is not None:
            kwargs['srcSRS'] = proj

        if use_bounds:
            kwargs['outputBounds'] = file_bounds
            print("Using bounds of", file_bounds)

        osgeo.gdal.Warp(out_file, in_file, **kwargs)
        _update_status({
            'status': "Processing hillshade data...",
            'progress': (idx / num_files) * 100
        }, req_id
        )

    return files


def _clear_uploads(data):
    uploaded_file = data.get('hillshade_file')
    if not uploaded_file:
        return

    world_file = os.path.basename(uploaded_file)
    extension = world_file[world_file.index('.') + 1:]
    wf_ext = f'{extension[0]}{extension[-1]}w'
    wf_name = world_file[:world_file.index('.')]
    world_file = f'{wf_name}.{wf_ext}'
    try:
        os.remove(os.path.join(os.path.dirname(uploaded_file),
                               world_file)
                  )
    except FileNotFoundError:
        print("Unable to remove world fie")
        print(world_file)

    # Done with the uploaded file (if any), delete it
    try:
        os.remove(uploaded_file)
    except FileNotFoundError:
        print("Unable to remove upload")


def _set_hillshade(data, zoom, map_bounds, req_id):
    tmp_dir = None
    if zoom < 7:
        hillshade_files = ["@earth_relief_15s"]
    elif zoom < 10.5:
        hillshade_files = ["@srtm_relief_01s"]
    else:
        # For higher zooms, use elevation.alaska.gov data
        tmp_dir = tempfile.TemporaryDirectory()
        _update_status("Downloading hillshade files...", req_id, data)

        tiff_dir = _download_elevation(map_bounds, tmp_dir, req_id)
        print("Generating composite hillshade file")

        _update_status("Processing hillshade data...", req_id, data)

        all_files = [os.path.join(tiff_dir, x) for x in os.listdir(tiff_dir)]
        out_files = _process_files(tiff_dir, all_files, map_bounds, req_id)

        hillshade_files = out_files

    # If a file was uploaded, add it at the end so it overlays anything else.
    # See if we have a file to deal with for this
    uploaded_file = data.get('hillshade_file')
    if uploaded_file:
        # See if we need to process this
        img_type = data['imgType']
        if img_type == 'j':
            # Image/World files need to be combined, along with their specified projection
            proj = data['imgProj']
            out_dir = os.path.dirname(uploaded_file)

            _update_status("Processing uploads...", req_id, data)

            out_file = _process_files(out_dir, [uploaded_file], map_bounds, req_id, proj=proj)
            out_file = out_file[0]

            uploaded_file = out_file

        hillshade_files.append(uploaded_file)

    return (hillshade_files, tmp_dir)


def _draw_hillshades(hillshade_file, fig, req_id, data, **kwargs):
    if not isinstance(hillshade_file, (list, tuple)):
        hillshade_file = [hillshade_file, ]

    multi_status = True
    num_files = len(hillshade_file)
    if num_files == 1:
        multi_status = False
        _update_status("Drawing map image...", req_id, data)

    for idx, file in enumerate(hillshade_file):
        if not file.startswith("@") and not os.path.isfile(file):
            continue  # Probably paranoid, but...

        if multi_status:
            _update_status(
                {
                    'status': "Drawing map image...",
                    'progress': (idx / num_files) * 100
                }, req_id,
                data
            )

        print("Adding image", idx, "of", len(hillshade_file), ":", file)
        fig.grdimage(file, **kwargs)

        # Done with the uploaded file (if any), delete it
        try:
            os.remove(file)
        except FileNotFoundError:
            print("Unable to remove upload")


def _add_stations(stations, fig, req_id, data):
    print("Plotting stations")
    _update_status("Plotting Stations...", req_id, data)

    main_dir = os.path.dirname(__file__)
    img_dir = os.path.join(main_dir, 'static/img')
    os.chdir(img_dir)

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
    used_symbols = {}

    for station in data.get('station', []):
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
            fig.image(icon_path, position=position)

    return used_symbols


def generate(req_id):
    data = _global_session.get(req_id)
    width = data['width']
    bounds = data['bounds']
    unit = data['unit']
    overview = data['overview']
    if overview == "False":
        overview = False

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

    hillshade_file, tmp_dir = _set_hillshade(data, data['mapZoom'], warp_bounds, req_id)

    hillshade_args = {
        "cmap": "topo",
        "nan_transparent": True,
        "dpi": 300,
        "shading": True
    }

    _draw_hillshades(hillshade_file, fig, req_id, data, **hillshade_args)

    _update_status("Drawing coastlines...", req_id, data)

    fig.coast(rivers='r/2p,#CBE7FF', water="#CBE7FF", resolution="f")

    if data['scale'] != 'False':
        _update_status("Adding Scale Bar...", req_id, data)

        # figure out middle latitude for map
        mid_lat = gmt_bounds[2] + ((gmt_bounds[3] - gmt_bounds[2]) / 2)
        map_width = vincenty.vincenty((mid_lat, gmt_bounds[0]),
                                      (mid_lat, gmt_bounds[1]))
        scale_length = math.ceil((map_width / 8))  # Make an even number
        offset = .65
        if data['scale'][0] == 'T':
            offset += .3

        offset = str(offset) + "c"

        if data['scale'][1] in ['L', 'R']:
            offset = '.375c/' + offset

        map_scale = f'j{data["scale"]}+w{scale_length}k+f+o{offset}+c{mid_lat}N+l'
        fig.basemap(map_scale = map_scale, F = '+gwhite+p')

    cur_dir = os.getcwd()

    used_symbols = _add_stations(data.get('station', []), fig, req_id, data)

    legend = data['legend']
    if legend != "False" and 'station' in data:
        print("Adding legend")
        _update_status("Adding Legend...", req_id, data)

        with tempfile.NamedTemporaryFile('w+') as file:
            for idx, (name, symbol) in enumerate(used_symbols.items()):
                sym_label = name[:-4]

                # This section handles images rather than symbols. Hacky, and *hopefully*
                # not needed, but left in for now just in case.
                if isinstance(symbol, str):
                    file.write('G 8p\n')
                    file.write(f'I {symbol} 16p LM\n')
                    file.write('G -1l\n')
                    # file.write('G -4p\n')
                    file.write('P .38 - - - - - - -\n')
                    file.write(f'T {sym_label}\n')
                    file.write('G 1l\n')
                    file.write('G -5p\n')
                    continue

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

    _update_status("Adding Overview Map...", req_id, data)

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
            ) = data['overviewBounds']

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

    inset_maps = zip(
        data['insetBounds'],
        data['insetZoom'],
        data['insetLeft'],
        data['insetTop'],
        data['insetWidth'],
        data['insetHeight']
    )
    for bounds, zoom, left, top, width, height in inset_maps:
        inset_bounds = [
            bounds[0],
            bounds[2],
            bounds[1],
            bounds[3]
        ]

        hillshade_file, tmp_dir = _set_hillshade(data, zoom, bounds, req_id)
        pos = f"x{left}{unit}/{top}{unit}+w{width}{unit}/{height}{unit}+jTL"

        with fig.inset(position=pos, box="+gwhite+p1p"):
            hillshade_args = {
                "region": inset_bounds,
                "projection": "M?",
                "nan_transparent": True,
                "shading": True,
                "dpi": 300,
            }
            _draw_hillshades(hillshade_file, fig, req_id, data, **hillshade_args)

            fig.coast(water='#CBE7FF',
                      resolution='f')

    _clear_uploads(data)
    _update_status("Saving final image...", req_id, data)
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

    os.chmod(sock_file, 0o777)

    sock.listen(5)
    run_process(sock)
    os.unlink(sock_file)
