from apiflask import APIFlask

app = APIFlask(__name__)

from .file_cache import FileCache
_global_session = FileCache()

from . import main
