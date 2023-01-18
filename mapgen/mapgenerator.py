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
import time
import tempfile
import uuid
import zipfile

from collections import defaultdict
from io import BytesIO
from tempfile import NamedTemporaryFile

import numpy
import osgeo.gdal
import pandas
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
    from . import wingdbstub
except ImportError:
    pass

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


class MapGenerator:
    _volc_colors = {
        'RED': '#EC0000',
        'GREEN': '#87C264',
        'YELLOW': '#FFFF66',
        'ORANGE': '#FF9933',
        'UNASSIGNED': '#777777',
    }

    icon_images = {
        't': 'triangle.svg',
        'a': 'star.svg',
        'i': 'inverted_triangle.svg',
        'c': 'circle.svg',
        'd': 'diamond.svg',
        'g': 'octagon.svg',
        'h': 'hexagon.svg',
        'n': 'pentagon.svg',
        's': 'square.svg',
        'p': 'point.svg',
    }

    station_symbols = {
        'GPS': {'symbol': 'a',
                'color': '#FF0000', },
        'Seismometer': {'symbol': 'i',
                        'color': '#000000', },
        'Tiltmeter': {'symbol': 'ktiltmeter.eps/',
                      'color': '#FF0000', },
        'Temperature': {'symbol': 'ktemperature.eps/',
                        'color': '#FF0000', },
        'Camera': {'symbol': 'kwebcam.eps/',
                   'color': '#0000FF',  # Really, could be anything...
                   },
        'Gas': {
            'symbol': 'kgas.eps/',
            'color': '#777777',
        },
        'Infrasound': {
            'symbol': 'kinfrasound.eps/',
            'color': '#000000',
        },
        "User Defined": {
            'symbol': 'a',
            'color': '#FFFF00',
        },
        "volcano": {
            'symbol': 'tV',  # kvolcano/
        },
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
        self.gmt_bounds = []

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
        chunk_size = 1024 * 1024 * 1000  # 10 MB

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

        _t_start = time.time()
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
                for chunk in req.iter_content(chunk_size = None):
                    if chunk:
                        bytes_written = zf.write(chunk)
                        loaded_bytes += bytes_written
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
        logging.info("Downloaded files in %f", time.time() - _t_start)
        return tiff_dir

    def _process_files(self, all_files, warp_bounds, proj = None):
        osgeo.gdal.AllRegister()  # Why? WHY!?!? But needed...
        files = []
        num_files = len(all_files)
        for idx, in_file in enumerate(all_files):
            logging.info(f"Processing image {idx+1} of {len(all_files)}")

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
                'progress': ((idx + 1) / num_files) * 100
            })

        return files

    def _set_hillshade(self, zoom, map_bounds):
        if zoom <= 7:
            hillshade_files = ["@earth_relief_15s"]
        elif zoom < 10:
            hillshade_files = ["@earth_relief_01s"]
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
                    'progress': ((idx + 1) / num_files) * 100
                })

            logging.info(f"Adding image {idx+1} of {len(hillshade_file)}: {file}")
            cm = self.data.get('mapColormap')
            if cm:
                try:
                    import pygmt
                except Exception:
                    os.environ['GMT_LIBRARY_PATH'] = '/usr/local/lib'
                    import pygmt

                pygmt.makecpt(cmap = cm, series = (-11000, 8500))
            self.fig.grdimage(file, **kwargs)

    def _add_stations(self, stations, zoom):
        logging.info("Plotting stations")
        self._update_status("Plotting Stations...")

        main_dir = os.path.dirname(__file__)
        img_dir = os.path.join(main_dir, 'static/img')
        os.chdir(img_dir)

        sym_outline = "faint,128" if zoom < 10 else 'thin,128'

        sym_size = (8 / 3) * zoom - (13 + (1 / 3))
        if sym_size < 8:
            sym_size = 8

        stations = self.data.get('station', [])
        sta_count = len(stations)

        plot_defs = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        volcNamePos = self.data['showVolcNames']
        volc_names = []
        
        # Pull the user-selected icons/colors from the HTTP request data
        custom_symbols = self.station_symbols.copy()
        legend_labels = {}
        for name, symbol, color, label in zip(self.data['staOpt_Name'],
                                              self.data['staOpt_Icon'],
                                              self.data['staOpt_Color'],
                                              self.data['staOpt_Label']):
            if symbol.endswith('.eps'):
                symbol = f"k{symbol}/"
            if symbol.startswith('k') and not symbol.endswith('/'):
                symbol += '/'

            custom_symbols[name]['symbol'] = symbol
            custom_symbols[name]['color'] = color
            legend_labels[name] = label

        for station in stations:
            category = station.get('category', 'Unknown')
            if isinstance(category, dict):
                category = category['type']

            sta_x = float(station['lon'])
            sta_y = float(station['lat'])

            if category.startswith('volcano'):
                outline = "thin,0"
                use_color = self.data['showVolcColor']

                if use_color:
                    color = self._volc_colors.get(category.replace('volcano', ''), 'white')
                else:
                    color = 'white'

                category = 'volcano'
                point_size = sym_size * 1.25

            else:
                point_size = sym_size
                color = custom_symbols.get(category, {}).get('color', '#FF00FF')

            symbol = custom_symbols.get(category, {}).get('symbol', 'a')
            if symbol is None:
                continue

            point_size_str = f"{point_size}p"
            symbol += point_size_str

            if symbol.startswith('tV') and volcNamePos != '':
                label_x = station.get('labelLon')
                label_y = station.get('labelLat')
                volc_names.append((station['name'], label_x, label_y))

            plot_defs[symbol][color]['x'].append(sta_x)
            plot_defs[symbol][color]['y'].append(sta_y)

            if not symbol.startswith('tV'):
                label = legend_labels[category]
                plot_defs[symbol][color]['label'] = label
                self._used_symbols[label] = {'symbol': symbol,
                                             'color': color, }

        complete = 0
        for symbol, sym_dict in plot_defs.items():
            for color, col_dict in sym_dict.items():
                x = col_dict['x']
                y = col_dict['y']
                label = col_dict.get('label')
                outline = sym_outline
                plot_symbol = symbol
                if symbol.startswith('tV'):  # this is a volcano marker
                    plot_symbol = symbol.replace('V', '')
                    outline = "thin,0"

                self.fig.plot(x=x, y=y, style=plot_symbol,
                              fill=color, pen = outline)

                complete += len(x)
                prog = round((complete / sta_count) * 100, 1)
                self._update_status({
                    'status': "Plotting Stations...",
                    'progress': prog
                })

        if volc_names:
            try:
                import pygmt
            except Exception:
                os.environ['GMT_LIBRARY_PATH'] = '/usr/local/lib'
                import pygmt
                
            vnames, vx, vy = zip(*volc_names)
            font_str = f"8p,Helvetica,black"
            with pygmt.config(FONT_ANNOT_PRIMARY = font_str):
                # Plot the names using standard positioning    
                self.fig.text(
                    x = vx, y = vy,
                    text = vnames,
                    justify = 'TC',
                )

    def _plot_data(self, zoom):
        plotdata_file = self.data.get('plotDataFile')
        if plotdata_file is None:
            return  # No data to plot

        self._update_status("Adding plot data")

        sym_size = (8 / 4) * zoom - (13 + (1 / 3))
        symbol = f"c{sym_size}p"

        latcol = self.data.get('latcol', 'latitude')
        loncol = self.data.get('loncol', 'longitude')
        valcol = self.data.get('valcol', 'value')

        plot_data = pandas.read_csv(plotdata_file)
        logging.info("Plot data file:")
        logging.info(str(plot_data))

        latitudes = plot_data[latcol].to_numpy()
        longitudes = plot_data[loncol].to_numpy()
        values = plot_data[valcol].to_numpy()

        trans_level = self.data.get('dataTrans', 0)
        cm = self.data.get('colorMap')
        cm_min = self.data.get('cmMin')
        cm_max = self.data.get('cmMax')

        if cm_min is None:
            cm_min = values.min()
        if cm_max is None:
            cm_max = values.max()

        if (cm_max - cm_min) > 1e6:
            scaled_values = 2000.0 * (values - cm_min) / numpy.ptp(values) - 1000
            cm_min_scaled = scaled_values.min()
            cm_max_scaled = scaled_values.max()
        else:
            scaled_values = values
            cm_min_scaled = cm_min
            cm_max_scaled = cm_max

        logging.info(f"Min value: {cm_min_scaled} Max Value: {cm_max_scaled}")
        logging.info(f"Avg. Value: {scaled_values.mean()}")

        try:
            import pygmt
        except Exception:
            os.environ['GMT_LIBRARY_PATH'] = '/usr/local/lib'
            import pygmt

        pygmt.makecpt(cmap = cm, series = ("{:f}". format(cm_min_scaled),
                                           "{:f}". format(cm_max_scaled)),
                      background = "i")
        self.fig.plot(x = longitudes, y = latitudes, style = symbol,
                      color = scaled_values, cmap = True, transparency = trans_level)
        logging.info("data plotted!")

        cb_position = self.data.get('colorbar')
        if cb_position.lower() != 'false':
            logging.info("Creating colorbar labels")
            labels = numpy.linspace(cm_min, cm_max, 5)
            scaled_labels = numpy.linspace(cm_min_scaled, cm_max_scaled, 5)

            roundLevel = 0
            rounded_labels = labels.round(roundLevel)
            logging.info("Rounding labels")
            while len(numpy.unique(rounded_labels)) != len(rounded_labels):
                roundLevel += 1
                rounded_labels = labels.round(roundLevel)

            logging.info(f"Rounded labels to: {roundLevel}")
            if roundLevel == 0 and numpy.abs(rounded_labels).max() < 1e16:
                rounded_labels = rounded_labels.astype(int)

            # Shift the first label to the right a small amount so it actually shows up.
            # Try 1%
            scaled_labels[0] += (scaled_labels.max() - scaled_labels.min()) / 1600

            # Create an annotation file to pass to the colorbar function
            logging.info("Creating colorbar file")
            with NamedTemporaryFile('w') as file:
                for pos, label in zip(scaled_labels, rounded_labels):
                    file.write(f"{pos} a {label}\n")

                file.flush()
                file.seek(0)  # Not sure if this is neccesary

                file_path = os.path.dirname(file.name)
                file_name = os.path.basename(file.name)
                cur_dir = os.getcwd()
                os.chdir(file_path)

                pos = f"J{cb_position}"
                frame = [f"pxc{file_name}"]
                if self.data.get('showCMTitle'):
                    frame.append(f"x+l{valcol}")

                scale_units = self.data.get('scaleunits')
                if scale_units is not None and scale_units:
                    frame.append(f"py+L{scale_units}")

                logging.info("Adding colorbar")
                logging.info(pos)
                logging.info(frame)
                self.fig.colorbar(position = pos, frame = frame)
                logging.info("Colorbar added")
                os.chdir(cur_dir)
            logging.info("Data plotted.")

    def _add_scalebar(self):
        if self.data['scale'] == 'False':
            return

        self._update_status("Adding Scale Bar...")

        # figure out middle latitude for map
        mid_lat = self.gmt_bounds[2] + ((self.gmt_bounds[3] - self.gmt_bounds[2]) / 2)
        map_width = vincenty.vincenty((mid_lat, self.gmt_bounds[0]),
                                      (mid_lat, self.gmt_bounds[1]))
        scale_length = int(math.ceil((map_width / 8)))  # Make an whole number
        if scale_length > 10 and scale_length < 75:
            # round to the nearest 10
            scale_length = int(round(scale_length / 10) * 10)
        elif scale_length >= 75:
            # round to the nearest 100
            scale_length = int(round(scale_length / 100) * 100)

        offset = .65
        if self.data['scale'][0] == 'T':
            offset += .3

        offset = str(offset) + "c"

        if self.data['scale'][1] in ['L', 'R']:
            offset = '.575c/' + offset

        map_scale = f'j{self.data["scale"]}+w{scale_length}k+f+o{offset}+c{mid_lat}N+l'
        self.fig.basemap(map_scale = map_scale, box = '+gwhite+p')

    def _gen_fail_callback(self, req_id, error):
        print("Map generation failed! Error:")
        print(error)
        print("-->{}<--".format(error.__cause__))
        data = _global_session[req_id]
        data['gen_status'] = "FAILED"
        _global_session[req_id] = data

    def generate(self, queue, req_id):
        logging.info("Starting generation process")
        self._socket_queue = queue
        try:
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
            self.gmt_bounds = [
                float(sw_lng),
                float(ne_lng),
                float(sw_lat),
                float(ne_lat)
            ]

            warp_bounds = [
                self.gmt_bounds[0],  # min x
                self.gmt_bounds[2],  # min y
                self.gmt_bounds[1],  # max x
                self.gmt_bounds[3]  # max y
            ]

            #     utm_left = self.gmt_bounds[0]
            #     if utm_left < -180:
            #         utm_left += 360

            #     utm_zone = math.ceil((utm_left + 180) / 6)
            #
            #     UTMChars = "CDEFGHJKLMNPQRSTUVWXX"
            #     utm_lat = self.gmt_bounds[2]
            #     if -80 <= utm_lat <= 84:
            #         utm_char = UTMChars[math.floor((utm_lat + 80) / 8)]
            #     else:
            #         utm_char = UTMChars[-1]
            #     proj = f"U{utm_zone}{utm_char}/{width}{unit}"

            # For albers equal area. Need to calculate center lon/lat
            # and top/bottom lon for parameters.
            # proj = f"B160/60/40/70/{width}{unit}"

            proj = f"M{width}{unit}"
            self.fig = pygmt.Figure()

            frame_ticks = ['n', 's', 'e', 'w']
            for idx in self.data['tickLabels']:
                frame_ticks[idx] = frame_ticks[idx].upper()

            basemap_args = {
                'projection': proj,
                'region': self.gmt_bounds,
                'frame': (''.join(frame_ticks), 'af')
            }

            frame_type = self.data['mapFrame']
            pygmt.config(MAP_FRAME_TYPE = frame_type)

            self.fig.basemap(**basemap_args)

            zoom = self.data['mapZoom']
            hillshade_file = self._set_hillshade(zoom, warp_bounds)

            hillshade_args = {
                "nan_transparent": True,
                "dpi": 300,
                "shading": True
            }

            self._draw_hillshades(hillshade_file, **hillshade_args)

            if self.data['fillOcean']:
                self._update_status("Drawing coastlines...")
                self.fig.coast(rivers='r/2p,#CBE7FF', water="#CBE7FF", resolution="f")

            self._plot_data(zoom)

            if self.data['showGrid']:
                logging.info("Adding Gridlines")
                self.fig.basemap(frame = 'g')

            logging.info("Getting ready to add stations")
            cur_dir = os.getcwd()

            stations = self.data.get('station', [])
            self._add_stations(stations, zoom)

            logging.info("Adding scalebar")
            self._add_scalebar()

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
                    x_loc = self.gmt_bounds[0] + (self.gmt_bounds[1] - self.gmt_bounds[0]) / 2
                    y_loc = self.gmt_bounds[2] + (self.gmt_bounds[3] - self.gmt_bounds[2]) / 2
                    self.fig.plot(x=[x_loc, ], y=[y_loc, ],
                                  style=f"a{star_size}", fill="blue")

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

                    self._add_stations(stations, zoom)

            legend = self.data['legend']

            if legend != "False" and len(self._used_symbols) > 0:
                logging.info("Adding legend")
                self._update_status("Adding Legend...")

                font_size = self.data['legendTextSize']
                icon_size = 1.333333 * font_size
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
                        # file.write(f'S 11p {sym_char} 16p {sym_color} - 23p {sym_label}')
                        file.write(f'S - {sym_char} {icon_size}p {sym_color} - - {sym_label}')
                        file.write('\n')

                    file.seek(0)
                    file_name = file.name

                    if use_width:
                        pos += "+w1.5i"

                    # Set FONT_ANNOT_PRIMARY for use in drawing legend
                    font_color = self.data['legendTextColor']
                    font_str = f"{font_size}p,Helvetica,{font_color}"
                    bkg_color = self.data['legendBkgColor']
                    bkg_transp = self.data['legendBkgTransp']
                    box_fill = f"+g{bkg_color}@{bkg_transp}+p1p"
                    with pygmt.config(FONT_ANNOT_PRIMARY = font_str):
                        self.fig.legend(
                            file_name,
                            position = pos,
                            box=box_fill
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
            self.fig.savefig(file_path, resize = "+m.25i", anti_alias = True)
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
        except Exception as e:
            self._socket_queue.send('ERROR')
            self._gen_fail_callback(req_id, e)


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
