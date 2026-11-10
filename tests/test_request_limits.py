from __future__ import annotations

import io
import typing as t

import pytest

from flask import Flask
from flask import Request
from flask import request
from flask import RequestLimits


class CountingStream(io.BytesIO):
    """Input stream that records how many bytes were actually read."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk

    def readinto(self, b: bytearray) -> int | None:
        n = super().readinto(b)
        self.bytes_read += n or 0
        return n


def multipart_body(
    fields: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes]] | None = None,
    boundary: str = "boundary",
) -> tuple[bytes, str]:
    """Build a multipart body and its content type."""
    out: list[bytes] = []

    for name, (filename, data) in (files or {}).items():
        out.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}";'
            f' filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode()
            + data
            + b"\r\n"
        )

    for name, value in (fields or {}).items():
        out.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            + value.encode()
            + b"\r\n"
        )

    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def test_default_limit_rejects_large_body(app, client):
    app.config["MAX_CONTENT_LENGTH"] = 16
    called = False

    @app.route("/echo", methods=["POST"])
    def echo():
        nonlocal called
        data = request.get_data()
        called = True
        return data

    stream = CountingStream(b"x" * 100)
    rv = client.post(
        "/echo", input_stream=stream, content_type="text/plain", content_length=100
    )
    assert rv.status_code == 413
    assert not called
    # The declared Content-Length exceeds the limit, so the body is
    # rejected before any data is read.
    assert stream.bytes_read == 0


def test_small_form_passes(app, client):
    app.config["MAX_CONTENT_LENGTH"] = 1024

    @app.route("/echo", methods=["POST"])
    def echo():
        return request.form["a"]

    rv = client.post("/echo", data={"a": "b"})
    assert rv.status_code == 200
    assert rv.text == "b"


def test_per_endpoint_limit(app, client):
    app.config["MAX_CONTENT_LENGTH"] = 16
    app.request_limits["import"] = RequestLimits(max_content_length=10_000)

    @app.route("/small", methods=["POST"])
    def small():
        return request.get_data()

    @app.route("/import", methods=["POST"], endpoint="import")
    def import_():
        return str(len(request.get_data()))

    rv = client.post("/small", data=b"x" * 100, content_type="text/plain")
    assert rv.status_code == 413

    rv = client.post("/import", data=b"x" * 100, content_type="text/plain")
    assert rv.status_code == 200
    assert rv.text == "100"


def test_endpoint_limit_applies_before_before_request(app, client):
    """An extension parsing the body in before_request is constrained by
    the per-endpoint policy, not a later or global one."""
    app.config["MAX_CONTENT_LENGTH"] = 16
    app.request_limits["import"] = RequestLimits(max_content_length=10_000)
    seen: list[int | None] = []

    @app.before_request
    def parse_early():
        seen.append(request.max_content_length)
        request.get_data()

    called = False

    @app.route("/import", methods=["POST"], endpoint="import")
    def import_():
        nonlocal called
        called = True
        return "ok"

    @app.route("/small", methods=["POST"])
    def small():
        nonlocal called
        called = True
        return "ok"

    rv = client.post("/import", data=b"x" * 100, content_type="text/plain")
    assert rv.status_code == 200
    assert called
    assert seen == [10_000]

    called = False
    rv = client.post("/small", data=b"x" * 100, content_type="text/plain")
    assert rv.status_code == 413
    assert not called
    assert seen == [10_000, 16]


def test_unmatched_route_uses_app_defaults(app, client):
    app.config["MAX_CONTENT_LENGTH"] = 16
    app.request_limits["import"] = RequestLimits(max_content_length=10_000)

    @app.before_request
    def read_body():
        request.get_data()

    rv = client.post("/nonexistent", data=b"x" * 100, content_type="text/plain")
    assert rv.status_code == 413


def test_select_request_limits_override(app, client):
    class App(Flask):
        def select_request_limits(self, endpoint):
            if endpoint == "big":
                return RequestLimits(max_content_length=10_000)

            return super().select_request_limits(endpoint)

    other = App("other")
    other.config["MAX_CONTENT_LENGTH"] = 16

    @other.route("/big", methods=["POST"])
    def big():
        return str(len(request.get_data()))

    rv = other.test_client().post("/big", data=b"x" * 100)
    assert rv.status_code == 200
    assert rv.text == "100"


def test_json_body_limit(app, client):
    app.config["MAX_CONTENT_LENGTH"] = 64

    @app.route("/json", methods=["POST"])
    def json_view():
        return request.get_json()

    data = b'{"data": "' + b"x" * 500 + b'"}'
    stream = CountingStream(data)
    rv = client.post(
        "/json",
        input_stream=stream,
        content_type="application/json",
        content_length=len(data),
    )
    assert rv.status_code == 413
    assert stream.bytes_read == 0


def test_no_content_length_reads_up_to_limit(app, client):
    """Without Content-Length, the limit is enforced while reading."""
    from werkzeug.test import EnvironBuilder

    app.config["MAX_CONTENT_LENGTH"] = 64
    called = False

    @app.before_request
    def parse_early():
        _ = request.form

    @app.route("/upload", methods=["POST"])
    def upload():
        nonlocal called
        called = True
        return "ok"

    body, content_type = multipart_body(fields={"a": "x" * 500})
    stream = CountingStream(body)
    builder = EnvironBuilder(
        "/upload", method="POST", input_stream=stream, content_type=content_type
    )
    req = builder.get_request()
    # The builder detects the length from the stream, remove it to
    # simulate a request without a Content-Length header.
    req.environ.pop("CONTENT_LENGTH", None)
    req.environ["wsgi.input_terminated"] = True
    rv = client.open(req)
    assert rv.status_code == 413
    assert not called
    # Reading stopped exactly at the limit, the rest was never consumed.
    assert stream.bytes_read == 64


def test_multipart_field_memory_limit(app, client):
    app.request_limits["upload"] = RequestLimits(max_form_memory_size=64)

    @app.route("/upload", methods=["POST"])
    def upload():
        return request.form["field"]

    body, content_type = multipart_body(fields={"field": "x" * 500})
    rv = client.post(
        "/upload",
        input_stream=CountingStream(body),
        content_type=content_type,
        content_length=len(body),
    )
    assert rv.status_code == 413


def test_multipart_parts_limit(app, client):
    app.config["MAX_FORM_PARTS"] = 2

    @app.route("/upload", methods=["POST"])
    def upload():
        return str(len(request.form))

    body, content_type = multipart_body(
        fields={f"f{i}": "x" * 100_000 for i in range(5)}
    )
    stream = CountingStream(body)
    rv = client.post(
        "/upload",
        input_stream=stream,
        content_type=content_type,
        content_length=len(body),
    )
    assert rv.status_code == 413
    # Parsing stopped at the third part, well before the end of the body.
    assert 0 < stream.bytes_read < len(body)


def test_mid_stream_failure_closes_uploads(app, client):
    created: list[t.IO[bytes]] = []

    class TrackingRequest(Request):
        def _get_file_stream(self, *args, **kwargs):
            stream = super()._get_file_stream(*args, **kwargs)
            created.append(stream)
            return stream

    class FailingStream(CountingStream):
        def readinto(self, b):
            if self.bytes_read > 0:
                raise ConnectionError("client went away")

            return super().readinto(b)

    app.request_class = TrackingRequest
    called = False

    @app.route("/upload", methods=["POST"])
    def upload():
        nonlocal called
        _ = request.files
        called = True
        return "ok"

    body, content_type = multipart_body(
        files={"f": ("f.txt", b"x" * 100_000)}, fields={"a": "b"}
    )
    stream = FailingStream(body)
    rv = client.post(
        "/upload",
        input_stream=stream,
        content_type=content_type,
        content_length=len(body),
    )
    assert rv.status_code == 400
    assert not called
    assert created
    assert all(s.closed for s in created)


def test_parsed_files_closed_when_rejected(app, client):
    """If a request is rejected after parsing, opened uploads are closed."""
    created: list[t.IO[bytes]] = []

    class TrackingRequest(Request):
        def _get_file_stream(self, *args, **kwargs):
            stream = super()._get_file_stream(*args, **kwargs)
            created.append(stream)
            return stream

    app.request_class = TrackingRequest

    @app.before_request
    def parse_early():
        _ = request.files

    @app.before_request
    def reject():
        return "nope", 413

    called = False

    @app.route("/upload", methods=["POST"])
    def upload():
        nonlocal called
        called = True
        return "ok"

    body, content_type = multipart_body(files={"f": ("f.txt", b"x" * 100)})
    rv = client.post(
        "/upload",
        input_stream=CountingStream(body),
        content_type=content_type,
        content_length=len(body),
    )
    assert rv.status_code == 413
    assert not called
    assert created
    assert all(s.closed for s in created)


def test_failed_parse_closes_uploads(app, client):
    """Uploads opened before a limit is hit mid-parse are closed."""
    created: list[t.IO[bytes]] = []

    class TrackingRequest(Request):
        def _get_file_stream(self, *args, **kwargs):
            stream = super()._get_file_stream(*args, **kwargs)
            created.append(stream)
            return stream

    app.request_class = TrackingRequest
    app.config["MAX_FORM_PARTS"] = 1

    @app.route("/upload", methods=["POST"])
    def upload():
        _ = request.form
        return "ok"

    body, content_type = multipart_body(
        files={"f": ("f.txt", b"x" * 100)}, fields={"big": "y" * 500}
    )
    rv = client.post(
        "/upload",
        input_stream=CountingStream(body),
        content_type=content_type,
        content_length=len(body),
    )
    assert rv.status_code == 413
    assert created
    assert all(s.closed for s in created)


@pytest.mark.parametrize("reader", ["get_data", "form", "files"])
@pytest.mark.parametrize(
    "limit", ["max_content_length", "max_form_memory_size", "max_form_parts"]
)
def test_relax_limit_after_read_raises(app, reader, limit):
    """Relaxing any limit after the body is read fails, regardless of
    which accessor consumed the body first."""
    app.config["MAX_CONTENT_LENGTH"] = 1024

    with app.test_request_context(
        "/upload",
        method="POST",
        data={"f": (io.BytesIO(b"data"), "f.txt"), "x": "y"},
    ):
        if reader == "get_data":
            request.get_data()
        elif reader == "form":
            _ = request.form
        else:
            _ = request.files

        current = getattr(request, limit)

        with pytest.raises(RuntimeError, match="Cannot relax"):
            setattr(request, limit, current + 1)

        with pytest.raises(RuntimeError, match="Cannot relax"):
            setattr(request, limit, None)

        # Tightening after reading is still allowed.
        setattr(request, limit, current - 1)
        assert getattr(request, limit) == current - 1


def test_apply_limits_after_read_raises(app):
    app.config["MAX_CONTENT_LENGTH"] = 1024

    with app.test_request_context(
        "/upload", method="POST", data=b"x" * 10, content_type="text/plain"
    ):
        request.get_data()

        with pytest.raises(RuntimeError, match="Cannot relax"):
            request.apply_request_limits(RequestLimits(max_content_length=10_000))


def test_relax_before_read_allowed(app):
    with app.test_request_context(
        "/upload", method="POST", data=b"x" * 10, content_type="text/plain"
    ):
        request.max_content_length = 10_000
        assert request.max_content_length == 10_000
        assert request.get_data() == b"x" * 10
