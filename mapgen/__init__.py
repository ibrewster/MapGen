try:
    from . import wingdbstub
except ImportError:
    pass

import logging

from apiflask import APIFlask
from flask_session import Session
from .flask_sockets.flask_sockets import Sockets

logging.basicConfig(level = logging.INFO)

app = APIFlask(__name__)
app.config['SESSION_TYPE'] = 'filesystem'
session = Session(app)
sockets = Sockets(app)


from .file_cache import FileCache
_global_session = FileCache()

from . import main
