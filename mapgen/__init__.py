try:
    from . import wingdbstub
except ImportError:
    pass

import logging

import flask
from flask_session import Session
from flask_sock import Sock

logging.basicConfig(level = logging.INFO)

app = flask.Flask(__name__)
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_KEY_PREFIX'] = "MapGenSession:"
app.config['TEMPLATES_AUTO_RELOAD'] = True

session = Session(app)
sockets = Sock(app)


from .file_cache import FileCache
_global_session = FileCache()

from . import main
