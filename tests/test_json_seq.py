import contextlib
import io

import pytest

import flask
from flask.json_seq import JsonSeqError


class DripStream(io.RawIOBase):
    """A stream that returns at most one byte per read, forcing the parser
    to handle record separators, JSON tokens, and UTF-8 characters split
    across read chunks.
    """

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.read_calls = 0

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1

        if self.pos >= len(self.data):
            return b""

        chunk = self.data[self.pos : self.pos + 1]
        self.pos += 1
        return chunk

    def readinto(self, b: bytearray) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_END:
            self.pos = len(self.data) + offset
        elif whence == io.SEEK_CUR:
            self.pos += offset
        else:
            self.pos = offset

        return self.pos

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True


@contextlib.contextmanager
def seq_request(app, data: bytes):
    """Push a request context with an application/json-seq body served one
    byte at a time. Yields the request and its drip stream.
    """
    stream = DripStream(data)

    with app.test_request_context(
        "/",
        method="POST",
        input_stream=stream,
        content_length=len(data),
        content_type="application/json-seq",
    ):
        yield flask.request, stream


def test_valid_corrupt_valid_order(app):
    data = b'\x1e{"a": 1}\n\x1e{"b": oops}\n\x1e{"c": "h\xc3\xa9llo \xe2\x86\x92"}\n'
    events = []

    def on_error(error):
        events.append(("error", error))

    with seq_request(app, data) as (request, stream):
        for value in request.iter_json_seq(on_error=on_error):
            events.append(("value", value))

    assert [kind for kind, _ in events] == ["value", "error", "value"]
    assert events[0][1] == {"a": 1}
    assert events[2][1] == {"c": "h\xe9llo \u2192"}

    error = events[1][1]
    assert isinstance(error, JsonSeqError)
    assert error.index == 1
    assert error.category == "invalid"
    assert error.start == data.index(b'{"b"')
    assert error.end == data.index(b"\x1e", error.start)
    assert data[error.start : error.end] == b'{"b": oops}\n'


def test_strict_mode_raises(app):
    data = b'\x1e{"a": 1}\n\x1enope\n\x1e{"b": 2}\n'

    with seq_request(app, data) as (request, stream):
        it = request.iter_json_seq()
        assert next(it) == {"a": 1}

        with pytest.raises(JsonSeqError) as exc_info:
            next(it)

        assert exc_info.value.index == 1
        assert exc_info.value.category == "invalid"


def test_truncated_record(app):
    data = b'\x1e{"a": 1}\n\x1e{"b": 2'
    errors = []

    with seq_request(app, data) as (request, stream):
        values = list(request.iter_json_seq(on_error=errors.append))

    assert values == [{"a": 1}]
    assert len(errors) == 1
    assert errors[0].index == 1
    assert errors[0].category == "truncated"
    assert errors[0].start == data.index(b'{"b"')
    assert errors[0].end == len(data)


def test_scalar_truncation_not_detectable(app):
    # RFC 7464: truncation of top-level numbers, true, false, and null is
    # not detectable, so unterminated scalars are yielded as-is.
    data = b"\x1e123\x1etrue\x1enull"
    errors = []

    with seq_request(app, data) as (request, stream):
        values = list(request.iter_json_seq(on_error=errors.append))

    assert values == [123, True, None]
    assert errors == []


def test_empty_records_skipped(app):
    data = b"\x1e\x1e\n\x1e1\n\x1e\x1e2\n\x1e"

    with seq_request(app, data) as (request, stream):
        assert list(request.iter_json_seq()) == [1, 2]


def test_max_record_bytes(app):
    big = b'{"big": "' + b"x" * 100 + b'"}'
    data = b"\x1e" + big + b"\n" + b'\x1e{"ok": true}\n'
    errors = []

    with seq_request(app, data) as (request, stream):
        values = list(
            request.iter_json_seq(on_error=errors.append, max_record_bytes=16)
        )

    assert values == [{"ok": True}]
    assert len(errors) == 1
    assert errors[0].index == 0
    assert errors[0].category == "too_large"
    assert errors[0].start == 1
    assert errors[0].end == 1 + len(big) + 1


def test_max_record_bytes_strict(app):
    data = b"\x1e" + b"x" * 100 + b"\n"

    with seq_request(app, data) as (request, stream):
        with pytest.raises(JsonSeqError) as exc_info:
            list(request.iter_json_seq(max_record_bytes=16))

        assert exc_info.value.category == "too_large"


def test_oversized_record_discard_bounded(app):
    # A large oversized record is discarded while scanning for the next
    # separator, without buffering its contents.
    big = b'{"big": "' + b"x" * (1024 * 1024) + b'"}'
    data = b"\x1e" + big + b"\n" + b"\x1e[1, 2]\n"
    errors = []

    with seq_request(app, data) as (request, stream):
        values = list(
            request.iter_json_seq(on_error=errors.append, max_record_bytes=64)
        )

    assert values == [[1, 2]]
    assert [e.category for e in errors] == ["too_large"]
    assert errors[0].end == 1 + len(big) + 1


def test_no_preread_after_stop(app):
    first = b'\x1e{"a": 1}\n'
    rest = b'\x1e{"b": 2}\n\x1e{"c": 3}\n'
    data = first + rest

    with seq_request(app, data) as (request, stream):
        it = request.iter_json_seq()
        assert next(it) == {"a": 1}
        # Only the first record and its terminating separator were read. The
        # separator is the first byte of the remaining data.
        assert stream.pos == len(first) + 1
        read_calls = stream.read_calls
        it.close()
        # Stopping the iterator does not read the remaining records.
        assert stream.pos == len(first) + 1
        assert stream.read_calls == read_calls


def test_get_data_before_iterator_rejected(app):
    data = b'\x1e{"a": 1}\n'

    with seq_request(app, data) as (request, stream):
        request.get_data()

        with pytest.raises(RuntimeError):
            request.iter_json_seq()


def test_get_json_before_iterator_rejected(app):
    data = b'{"a": 1}'

    with seq_request(app, data) as (request, stream):
        request.get_json(force=True)

        with pytest.raises(RuntimeError):
            request.iter_json_seq()


def test_consumers_after_iterator_rejected(app):
    data = b'\x1e{"a": 1}\n'

    with seq_request(app, data) as (request, stream):
        request.iter_json_seq()

        with pytest.raises(RuntimeError):
            request.get_data()

        with pytest.raises(RuntimeError):
            request.get_json()

        with pytest.raises(RuntimeError):
            request.iter_json_seq()

        with pytest.raises(RuntimeError):
            _ = request.data

        with pytest.raises(RuntimeError):
            _ = request.form


def test_form_parsed_before_iterator_rejected(app):
    with app.test_request_context(
        "/",
        method="POST",
        data={"a": "b"},
        content_type="application/x-www-form-urlencoded",
    ):
        _ = flask.request.form

        with pytest.raises(RuntimeError):
            flask.request.iter_json_seq()


def test_normal_json_request_unchanged(app, client):
    @app.route("/json", methods=["POST"])
    def post_json():
        return flask.jsonify(flask.request.get_json())

    rv = client.post("/json", json={"a": 1})
    assert rv.status_code == 200
    assert rv.get_json() == {"a": 1}


def test_module_level_iterator():
    from flask.json_seq import iter_json_seq

    data = b'\x1e{"a": 1}\n\x1ebad\n\x1e[2]\n'
    errors = []
    values = list(
        iter_json_seq(io.BytesIO(data), flask.json.loads, on_error=errors.append)
    )
    assert values == [{"a": 1}, [2]]
    assert len(errors) == 1
    assert errors[0].index == 1
