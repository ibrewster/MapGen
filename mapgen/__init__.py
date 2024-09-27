try:
    from . import wingdbstub
except ImportError:
    pass

import logging
import os

import redis

home_dir = os.path.realpath(os.path.join(os.getcwd(), '..'))
os.environ["GMT_USERDIR"] = home_dir
os.environ['GMT_TMPDIR'] = home_dir
os.environ['PLOTLY_HOME'] = home_dir
os.environ["HOME"] = home_dir

import flask
from flask_session import Session
from flask_sock import Sock

logging.basicConfig(level = logging.INFO)

app = flask.Flask(__name__)

# REDIS session configuration

app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_KEY_PREFIX'] = "MapGenSession:"
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SESSION_REDIS'] = redis.StrictRedis(host='localhost', port=6379, db=0)  # URL of your Redis server


session = Session(app)
sockets = Sock(app)


from .file_cache import FileCache
_global_session = FileCache()

from . import main
