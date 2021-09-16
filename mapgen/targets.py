import os
import shutil
import tempfile

from functools import wraps
from pathlib import Path

import flask
import gevent

from apiflask import abort
from streaming_form_data.targets import BaseTarget, DirectoryTarget
from streaming_form_data import StreamingFormDataParser


def api_input(schema, **kwargs):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            with tempfile.TemporaryDirectory() as tempdir:
                try:
                    data = _parseFormData(schema, flask.request, tempdir)
                except ValueError as e:
                    return abort(400, "Missing Parameter:" + str(e))
                result = f(data)
            return result
        return wrapper
    return decorator


class BaseSchema:
    """Base class from which request parse schemas should inherit"""
    targets = None,
    defaults = None,
    _parser = None

    def __init__(self, parser, temp_dir = None):
        self.targets = {}
        self.defaults = {}
        self._parser = parser
        for field in (set(dir(self)) - set(dir(BaseSchema))):
            generator = getattr(self, field)
            if hasattr(generator, 'set_directory'):
                generator.set_directory(temp_dir)

            if hasattr(generator, 'default'):
                self.defaults[field] = generator.default

            self.targets[field] = generator.target()
            parser.register(field, self.targets[field])

    def parse(self, data):
        # chunk_size = 32768  # 32k
        while True:
            chunk = data.read()
            if len(chunk) == 0:
                break
            self._parser.data_received(chunk)
            gevent.sleep(0)

    def values(self):
        values = {}
        for field, target in self.targets.items():
            if not target.finished:
                try:
                    value = self.defaults[field]
                except KeyError:
                    raise ValueError(f"No value provided for field {field}, and no default")
            else:
                value = target.value

            values[field] = value

        return values


def _parseFormData(Schema, request, file_dir):
    headers = dict(request.headers)
    parser = StreamingFormDataParser(headers = headers)
    schema = Schema(parser, file_dir)  # materialize an instance of this schema
    schema.parse(request.stream)
    return schema.values()


class _fileResult:
    def __init__(self, filename, filedir):
        self._filename = filename
        self._filedir = filedir

    @property
    def name(self):
        return self._filename

    def save(self, dest):
        src_file = os.path.join(self._filedir, self._filename)
        dst_file = os.path.join(dest, self._filename)
        shutil.move(src_file, dst_file)


class _FileTarget(DirectoryTarget):
    def __init__(
        self,
        directory_path: str = None,
        allow_overwrite: bool = True,
        *args,
        **kwargs
    ):
        super().__init__(directory_path,
                         allow_overwrite,
                         *args,
                         **kwargs)

        self.directory_path = directory_path

        self._mode = 'wb' if allow_overwrite else 'xb'
        self._fd = None
        self.multipart_filenames: List[str] = []
        self.multipart_content_types: List[str] = []

    def set_directory(self, path):
        self.directory_path = path

    def on_start(self):
        if not self.multipart_filename:
            return  # No file to save

        if self.directory_path is None:
            raise ValueError("No path specified")

        # Path().resolve().name only keeps file name to prevent path traversal
        self.multipart_filename = Path(self.multipart_filename).resolve().name
        self._fd = open(
            Path(self.directory_path) / self.multipart_filename, self._mode
        )

    @property
    def value(self):
        return _fileResult(self.multipart_filename, self.directory_path)

    @property
    def finished(self):
        return self._finished


class _ListTarget(BaseTarget):
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
            pass  # already is bytes, no need to do anything
        else:
            value = self._type(value)

        self._values.append(value)

    @property
    def value(self):
        return self._values

    @property
    def finished(self):
        return self._finished


class _TypedTarget(BaseTarget):
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
        try:
            self.value
        except ValueError:
            # If we can't convert the value to the requested type,
            # see if it is because we have nothing. If so, do the
            # same as not being finished, i.e. either default or error
            # if no default provided.
            if not b''.join(self._values):
                return False

        return self._finished

    @property
    def value(self):
        value = b''.join(self._values)

        if self._type == str:
            value = value.decode('UTF-8')
        elif self._type == bytes:
            pass  # already is bytes, no need to do anything
        else:
            value = self._type(value)

        return value


class _TargetGenerator:
    def __init__(self, *args, **kwargs):
        self._adtl_args = list(args)
        for key, value in kwargs.items():
            setattr(self, key, value)

    def target(self, *args):
        args = self._adtl_args + list(args)
        res = self._target(*args)
        return res


class File(_TargetGenerator):
    """File upload form field"""
    _target = _FileTarget

    def set_directory(self, dest):
        self._file_dir = dest

    def target(self, *args):
        args = [self._file_dir] + self._adtl_args + list(args)
        res = self._target(*args)
        return res


class List(_TargetGenerator):
    """
    Typed, list value form field

    Parameters
    ----------
    type_ : callable
        A callable expecting a single argument (a bytes string), which
        it will convert to the desired type and return.
    """
    _target = _ListTarget

    def __init__(self, type_, **kwargs):
        super().__init__(type_, **kwargs)


class Value(_TargetGenerator):
    """
    Typed, single-value form field

    Parameters
    ----------
    type_ : callable
        A callable expecting a single argument (a bytes string), which
        it will convert to the desired type and return.
    """
    _target = _TypedTarget

    def __init__(self, type_, **kwargs):
        super().__init__(type_, **kwargs)
