from shapely.geometry import Polygon
import shapely_geojson

import errno
import logging
import math
import multiprocessing
import os
import pickle
import socket
import shutil
import signal
import tempfile
import uuid
import zipfile

from io import BytesIO

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

try:
    from . import utils
except ImportError:
    import utils


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
                    request_id = msg.get('data')
                    generator = MapGenerator(request_id)
                    pool.apply_async(generator.generate)
    except KeyboardInterrupt:
        return


def init_generator_proc(queue):
    global _SOCKET_QUEUE
    _SOCKET_QUEUE = queue

    logging.basicConfig(level = logging.INFO,
                        format = "%(asctime)-15s %(message)s",
                        datefmt='%Y-%m-%d %H:%M:%S')


class MapGenerator:
    station_symbols = {
        'GPS': {'symbol': 'a',
                'color': 'red', },
        'Seismometer': {'symbol': 't',
                        'color': 'green', },
        'Tiltmeter': {'symbol': 'ktiltmeter.eps/',
                      'color': 'blue', },
        'Camera': {'symbol': 'kwebcam.eps/',
                   'color': 'blue',  # Really, could be anything...
                   },
        'Gas': {
            'symbol': 'kgas.eps/',
            'color': 'gray',
        },
        'Infrasound': {
            'symbol': 'kinfrasound.eps/',
            'color': 'black',
        },
        "User Defined": {
            'symbol': 'a',
            'color': 'yellow',
        }
    }

    def __init__(self, req_id = None):
        self._tmp_dir = tempfile.mkdtemp()
        self._req_id = req_id
        if req_id is not None:
            self.data = _global_session[self._req_id]
        else:
            self.data = None
            
        self._used_symbols = {}
        self._socket_queue = None

    def setReqId(self, req_id):
        self._req_id = req_id
        self.data = _global_session[self._req_id]
        
    def tempdir(self):
        return self._tmp_dir

    def _update_status(self, status):
        self.data['gen_status'] = status
        _global_session[self._req_id] = self.data

        if self._socket_queue is not None:
            self._socket_queue.send(status)

    def _download_elevation(self, bounds):
        if bounds[0] < -180 or bounds[2] > 180 or bounds[0] > bounds[2]:
            # Crossing dateline. Need to split request.
            bounds = list(bounds)
            bounds2 = bounds.copy()
            bounds3 = bounds.copy()
            # Make bounds be only west of dateline
            if bounds3[0] < 0:
                bounds3[0] += 360
            bounds3[2] = 180

            bounds2[0] = -180
            if bounds2[2] > 0:
                bounds2[2] -= 360

            poly2 = Polygon.from_bounds(*bounds2)
            poly3 = Polygon.from_bounds(*bounds3)
            bounds_list = [shapely_geojson.dumps(poly3),
                           shapely_geojson.dumps(poly2)]
        else:
            poly = Polygon.from_bounds(*bounds)
            bounds_list = [shapely_geojson.dumps(poly), ]

        ids = 151  # DSM hillshade
        URL_BASE = 'https://elevation.alaska.gov'
        list_url = f'{URL_BASE}/query.json'
        url = f'{URL_BASE}/download'
        est_size = 0
        logging.info("Downloading hillshade files")
        tempdir = self.tempdir()
        zf_path = os.path.join(tempdir, 'custom_download.zip')
        tiff_dir = os.path.join(tempdir, 'tiffs')
        os.makedirs(tiff_dir, exist_ok=True)

        loaded_bytes = 0
        pc = 0
        chunk_size = 1024 * 1024 * 10  # 10 MB

        for geojson in bounds_list:
            # get file listings
            try:
                req = requests.post(list_url, data = {'geojson': geojson, })
            except requests.exceptions.ConnectionError:
                logging.warning("Connection error attempting to get file listings")
                continue

            if req.status_code != 200:
                logging.warning(f"Unable to get file listings. Server returned {req.status_code}")
            else:
                files = req.json()
                try:
                    file_info = next((x for x
                                      in files
                                      if x['dataset_id'] == ids))
                except StopIteration:
                    logging.warning("Requested dataset info not found in server response")
                else:
                    logging.debug(str(file_info))
                    est_size += file_info.get('bytes', -1)

        for geojson in bounds_list:
            req = requests.get(url,
                               params = {'geojson': geojson,
                                         'ids': ids},
                               stream = True)
            if req.status_code != 200:
                logging.warning(f"Unable to fetch hillshade files for region. Server returned {req.status_code}")
                print(req.status_code)
                print(req.text)
                continue

            with open(zf_path, 'wb') as zf:
                for chunk in req.iter_content(chunk_size = chunk_size):
                    if chunk:
                        loaded_bytes += zf.write(chunk)
                        if est_size > 0:
                            pc = round((loaded_bytes / est_size) * 100, 1)

                            self._update_status({
                                'status': "Downloading hillshade files...",
                                'progress': pc
                            })

            logging.info(f"Downloaded {loaded_bytes} bytes of hillshade files")

            # Pull out the various tiff files needed
            logging.info("Extracting tiffs")

            self._update_status("Decompressing hillshade data...")

            with zipfile.ZipFile(zf_path, 'r') as zf:
                for file in zf.namelist():
                    if file.endswith('.zip'):
                        logging.info(f"Reading {file}")
                        zf_data = BytesIO(zf.read(file))
                        with zipfile.ZipFile(zf_data, 'r') as zf2:
                            for tiffile in zf2.namelist():
                                if tiffile.endswith('.tif'):
                                    if os.path.isfile(os.path.join(tiff_dir, tiffile)):
                                        continue  # already extracted, move on
                                    logging.info(f"Extracting {tiffile}")
                                    zf2.extract(tiffile, path = tiff_dir)
        return tiff_dir

    def _process_files(self, all_files, warp_bounds, proj = None):
        osgeo.gdal.AllRegister()  # Why? WHY!?!? But needed...
        files = []
        num_files = len(all_files)
        for idx, in_file in enumerate(all_files):
            logging.info(f"Processing image {idx} of {len(all_files)}")

            in_path, in_ext = os.path.splitext(in_file)
            out_file = f"{in_path}-processed.tiff"
            # Considered skipping if already processed, but the bounds might be different
            # if os.path.isfile(out_file):
            # continue  # Already processed. Move on.

            ds = osgeo.gdal.Open(in_file)
            file_bounds = utils.get_extents(ds, proj)
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
                logging.info(f"Using bounds of {file_bounds}")
                if ((file_bounds[0] < 0) == (file_bounds[2] < 0)) and file_bounds[0] > file_bounds[2]:
                    logging.warning("Skipping file due to negative bounds")
                    continue

                # this seems unlikely, but still would be wrong
                if ((file_bounds[0] < 0) != (file_bounds[2] < 0)) and file_bounds[0] < file_bounds[2]:
                    logging.warning("Skipping file due to really weird bounds")
                    continue

            osgeo.gdal.Warp(out_file, in_file, **kwargs)
            files.append(out_file)
            self._update_status({
                'status': "Processing hillshade data...",
                'progress': (idx / num_files) * 100
            })

        return files

    def _set_hillshade(self, zoom, map_bounds):
        if zoom <= 7:
            hillshade_files = ["@earth_relief_15s"]
        elif zoom < 10:
            hillshade_files = ["@srtm_relief_01s"]
        else:
            # For higher zooms, use elevation.alaska.gov data
            self._update_status("Downloading hillshade files...")

            tiff_dir = self._download_elevation(map_bounds)
            logging.info("Generating composite hillshade file")

            self._update_status("Processing hillshade data...")

            all_files = [os.path.join(tiff_dir, x) for x in os.listdir(tiff_dir)]
            out_files = self._process_files(all_files, map_bounds)

            hillshade_files = out_files

        # If a file was uploaded, add it at the end so it overlays anything else.
        # See if we have a file to deal with for this
        uploaded_file = self.data.get('hillshade_file')
        if uploaded_file:
            # See if we need to process this
            self._update_status("Processing uploads...")

            img_type = self.data['imgType']
            proj = None

            if img_type == 'j':
                # Image/World files need to have their projection specified. GeoTIFF files have it embeded.
                proj = self.data['imgProj']

            # Either way, we need to convert to lat/lon and trim to map area
            out_file = self._process_files([uploaded_file],
                                           map_bounds, proj=proj)
            if out_file:
                hillshade_files.append(out_file[0])

        return hillshade_files

    def _draw_hillshades(self, hillshade_file, **kwargs):
        if not isinstance(hillshade_file, (list, tuple)):
            hillshade_file = [hillshade_file, ]

        multi_status = True
        num_files = len(hillshade_file)
        if num_files == 1:
            multi_status = False
            self._update_status("Drawing map image...")

        for idx, file in enumerate(hillshade_file):
            if not file.startswith("@") and not os.path.isfile(file):
                continue  # Probably paranoid, but...

            if multi_status:
                self._update_status({
                    'status': "Drawing map image...",
                    'progress': (idx / num_files) * 100
                })

            logging.info(f"Adding image {idx} of {len(hillshade_file)}: {file}")
            self.fig.grdimage(file, **kwargs)

    def _add_stations(self, stations, zoom):
        if zoom < 8:
            return {}  # Don't plot stations at low zoom levels

        logging.info("Plotting stations")
        self._update_status("Plotting Stations...")

        main_dir = os.path.dirname(__file__)
        img_dir = os.path.join(main_dir, 'static/img')
        os.chdir(img_dir)

        sym_outline = "faint,128" if zoom < 10 else 'thin,128'

        sym_size = (8 / 3) * zoom - (13 + (1 / 3))

        sym_size = f"{sym_size}p"
        for station in self.data.get('station', []):
            category = station['category']
            sta_x = float(station['lon'])
            sta_y = float(station['lat'])

            symbol = self.station_symbols.get(category, {}).get('symbol', 'a')
            color = self.station_symbols.get(category, {}).get('color', '#FF00FF')

            if symbol is not None:
                symbol += sym_size
                self._used_symbols[category] = {'symbol': symbol,
                                                'color': color, }
                self.fig.plot(x=[sta_x, ], y=[sta_y, ],
                              style=symbol, color=color,
                              pen = sym_outline)
            # else:
                # icon_path = os.path.join(main_dir, 'static/img', icon_name)
                # used_symbols[icon_name] = icon_path

                # if not os.path.isfile(icon_path):
                # req = requests.get(icon_url)
                # if req.status_code != 200:
                # continue  # Can't get an icon for this station, move on.
                # with open(icon_path, 'wb') as icon_file:
                # icon_file.write(req.content)

                # position = f"g{sta_x}/{sta_y}+w{sym_size}"
                # fig.image(icon_path, position=position)

    def generate(self):
        logging.info("Starting generation process")
        self._socket_queue = _SOCKET_QUEUE
        self.data = _global_session.get(self._req_id)
        self._update_status("Initializing")
        logging.info("Sent first status update")
        width = self.data['width']
        bounds = self.data['bounds']
        unit = self.data['unit']
        overview = self.data['overview']
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
    #     proj = f"U{utm_zone}{utm_char}/{width}{unit}"

        proj = f"M{width}{unit}"
        self.fig = pygmt.Figure()

        basemap_args = {
            'projection': proj,
            'region': gmt_bounds,
            'frame': ('WeSn', 'afg'),
        }

        self.fig.basemap(**basemap_args)

        zoom = self.data['mapZoom']
        hillshade_file = self._set_hillshade(zoom, warp_bounds)

        hillshade_args = {
            "nan_transparent": True,
            "dpi": 300,
            "shading": True
        }

        self._draw_hillshades(hillshade_file, **hillshade_args)

        self._update_status("Drawing coastlines...")

        self.fig.coast(rivers='r/2p,#CBE7FF', water="#CBE7FF", resolution="f")

        if self.data['scale'] != 'False':
            self._update_status("Adding Scale Bar...")

            # figure out middle latitude for map
            mid_lat = gmt_bounds[2] + ((gmt_bounds[3] - gmt_bounds[2]) / 2)
            map_width = vincenty.vincenty((mid_lat, gmt_bounds[0]),
                                          (mid_lat, gmt_bounds[1]))
            scale_length = math.ceil((map_width / 8))  # Make an even number
            offset = .65
            if self.data['scale'][0] == 'T':
                offset += .3

            offset = str(offset) + "c"

            if self.data['scale'][1] in ['L', 'R']:
                offset = '.375c/' + offset

            map_scale = f'j{self.data["scale"]}+w{scale_length}k+f+o{offset}+c{mid_lat}N+l'
            self.fig.basemap(map_scale = map_scale, F = '+gwhite+p')

        cur_dir = os.getcwd()

        self._add_stations(self.data.get('station', []), zoom)

        if overview:
            logging.info("Adding Overview")
            self._update_status("Adding Overview Map...")

            ak_bounds = [
                -190.0, -147.68, 48.5, 69.5
            ]

            if self.data['overviewBounds']:
                (sw_lng,
                 sw_lat,
                 ne_lng,
                 ne_lat) = self.data['overviewBounds']

                ak_bounds = [
                    float(sw_lng),
                    float(ne_lng),
                    float(sw_lat),
                    float(ne_lat)
                ]

            inset_width = self.data['overviewWidth']
            pos = f"j{overview}+w{inset_width}{unit}+o0.1c"
            star_size = "16p"
            with self.fig.inset(position=pos, box="+gwhite+p1p"):
                self.fig.coast(
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
                self.fig.plot(x=[x_loc, ], y=[y_loc, ],
                              style=f"a{star_size}", color="blue")

        inset_maps = zip(self.data['insetBounds'],
                         self.data['insetZoom'],
                         self.data['insetLeft'],
                         self.data['insetTop'],
                         self.data['insetWidth'],
                         self.data['insetHeight'])

        for bounds, zoom, left, top, width, height in inset_maps:
            inset_bounds = [
                bounds[0],
                bounds[2],
                bounds[1],
                bounds[3]
            ]

            hillshade_file = self._set_hillshade(zoom, bounds)
            pos = f"x{left}{unit}/{top}{unit}+w{width}{unit}/{height}{unit}+jTL"

            with self.fig.inset(position=pos, box="+gwhite+p1p"):
                hillshade_args = {
                    "region": inset_bounds,
                    "projection": "M?",
                    "nan_transparent": True,
                    "shading": True,
                    "dpi": 300,
                }
                self._draw_hillshades(hillshade_file, **hillshade_args)

                self.fig.coast(water='#CBE7FF',
                               resolution='f')

                self._add_stations(self.data.get('station', []), zoom)

        legend = self.data['legend']
        if legend != "False" and 'station' in self.data:
            logging.info("Adding legend")
            self._update_status("Adding Legend...")

            with tempfile.NamedTemporaryFile('w+') as file:
                pos = f"J{legend}+j{legend}+o0.2c+l1.5"
                use_width = False
                for idx, (name, symbol) in enumerate(self._used_symbols.items()):
                    sym_label = name

                    # This section handles images rather than symbols. Hacky, and *hopefully*
                    # not needed, but left in for now just in case.
                    if isinstance(symbol, str):
                        use_width = True
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

                if use_width:
                    pos += "+w1.5i"

                self.fig.legend(
                    file_name,
                    position = pos,
                    box="+gwhite+p1p"
                )

        os.chdir(cur_dir)
        self._update_status("Saving final image...")
        save_file = f'{uuid.uuid4().hex}.pdf'

        # Save to a more perminant location so the results
        # don't get deleted until they are retrieved.
        script_dir = os.path.dirname(__file__)
        cache_dir = os.path.join(script_dir, "cache")
        os.makedirs(cache_dir, exist_ok = True)
        file_path = os.path.join(cache_dir, save_file)
        self.fig.savefig(file_path, anti_alias = True)
        self.data['map_file'] = file_path
        self.data['gen_status'] = "Complete"
        _global_session[self._req_id] = self.data
        self._update_status("COMPLETE")

        # Clean up the temporary directory
        logging.info(f"Cleaning up temporary directory {self.tempdir()}")
        try:
            shutil.rmtree(self.tempdir())
        except OSError as err:
            if err.errno != errno.ENOENT:
                raise
        logging.debug(str(file_path))


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
