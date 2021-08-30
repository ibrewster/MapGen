from pathlib import Path
from urllib.parse import unquote

import ujson

from streaming_form_data.targets import BaseTarget, DirectoryTarget

class FileTarget(DirectoryTarget):
    def on_start(self):
        if not self.multipart_filename:
            return # No file to save

        # Path().resolve().name only keeps file name to prevent path traversal
        self.multipart_filename = Path(self.multipart_filename).resolve().name
        self._fd = open(
            Path(self.directory_path) / self.multipart_filename, self._mode
        )
    
    @property
    def value(self):
        return self.multipart_filename
    
    @property
    def finished(self):
        return self._finished
    

class ListTarget(BaseTarget):
    """ValueTarget stores the input in an in-memory list of bytes.
    This is useful in case you'd like to have the value contained in an
    in-memory string.
    """

    def __init__(self, _type = bytes, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._temp_value = []
        self._values = []
        self._type = _type

    def on_data_received(self, chunk: bytes):
        self._temp_value.append(chunk)
        
    def on_finish(self):
        value = b''.join(self._temp_value)
        self._temp_value = []
        
        if self._type == str:
            value = value.decode('UTF-8')
        elif self._type == bytes:
            pass # already is bytes, no need to do anything
        else:
            value = self._type(value)

        self._values.append(value)        

    @property
    def value(self):
        return self._values
    
    @property
    def finished(self):
        return self._finished    
    
    
class TypedTarget(BaseTarget):
    """ValueTarget stores the input in an in-memory list of bytes.
    This is useful in case you'd like to have the value contained in an
    in-memory string.
    """

    def __init__(self, _type = bytes, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._values = []
        self._type = _type

    def on_data_received(self, chunk: bytes):
        self._values.append(chunk)
        
    @property
    def finished(self):
        return self._finished
        
    @property
    def value(self):
        value = b''.join(self._values)
        
        if self._type == str:
            value = value.decode('UTF-8')
        elif self._type == bytes:
            pass # already is bytes, no need to do anything
        else:
            value = self._type(value)

        return value
    

def Bounds(value):
    """Returns a sw_lng, sw_lat, ne_lng, ne_lat tupple"""
    if value:
        if isinstance(value, bytes):
            value = value.decode('UTF-8')
        try:
            return tuple(map(float, unquote(value).split(',')))
        except ValueError:
            return None

    return None

def JSON(value):
    if value:
        try:
            return ujson.loads(value)
        except ValueError:
            return None

    return None    