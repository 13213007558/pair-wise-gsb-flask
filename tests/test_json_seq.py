from __future__ import annotations

from io import BytesIO

import pytest

import flask
from flask.testing import EnvironBuilder
from flask.json_seq import ENCODING_ERROR
from flask.json_seq import SYNTAX_ERROR
from flask.json_seq import TOO_LARGE_ERROR
from flask.json_seq import TRUNCATED_ERROR


class OneByteStream(BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.read_count = 0

    def read(self, size: int | None = -1) -> bytes:
        if size == 1:
            self.read_count += 1
            return super().read(1)
        return super().read(size if size is not None else -1)

    def readinto(self, buffer) -> int:
        if len(buffer) == 1:
            self.read_count += 1
        return super().readinto(buffer)


@pytest.fixture
def seq_request(app):
    def make(body: bytes, content_type: str = "application/json-seq"):
        environ = EnvironBuilder(
            app,
            method="POST",
            path="/",
            data=body,
            content_type=content_type,
        ).get_environ()
        raw_stream = OneByteStream(body)
        environ["wsgi.input"] = raw_stream
        return app.request_context(environ), raw_stream

    return make


def test_stream_records_and_bad_record_callback(seq_request):
    body = b'\x1e{"ok": true}\x1ebad\x1e[1, 2]'
    ctx, raw_stream = seq_request(body)
    events = []

    with ctx:
        records = flask.request.iter_json_seq(
            strict=False,
            on_bad_record=lambda index, byte_range, category: events.append(
                (index, byte_range, category)
            ),
        )
        assert next(records) == {"ok": True}
        assert next(records) == [1, 2]

    assert events == [(1, (14, 17), SYNTAX_ERROR)]
    assert raw_stream.read_count == len(body)


def test_iterator_does_not_read_after_last_consumed_record(seq_request):
    body = b'\x1e{"first": true}\x1e{"second": true}'
    ctx, raw_stream = seq_request(body)

    with ctx:
        records = flask.request.iter_json_seq()
        assert next(records) == {"first": True}
        assert raw_stream.read_count == 16
        assert raw_stream.read(1) == b'\x1e'
        assert raw_stream.read_count == 17


@pytest.mark.parametrize(
    ("payload", "category"),
    [
        (b"12", TRUNCATED_ERROR),
        (b"tr", TRUNCATED_ERROR),
        (b"nul", TRUNCATED_ERROR),
        (b"12}\n", SYNTAX_ERROR),
        (b'{"a":', TRUNCATED_ERROR),
        (b'"unterminated', TRUNCATED_ERROR),
        (b'"\x5cq"', SYNTAX_ERROR),
        (b"\xff\xfe", ENCODING_ERROR),
    ],
)
def test_bad_record_categories(seq_request, payload, category):
    body = b"\x1e" + payload + b"\x1e{}\n"
    ctx, _ = seq_request(body)
    errors = []

    with ctx:
        records = flask.request.iter_json_seq(
            strict=False,
            on_bad_record=lambda index, byte_range, error: errors.append(
                (index, byte_range, error)
            ),
        )
        assert list(records) == [{}]

    assert errors == [
        (0, (1, 1 + len(payload)), category),
    ]


def test_utf8_and_tokens_can_cross_reads(seq_request):
    snowman = "\u2603".encode()
    body = b'\x1e"' + snowman + b'"\n\x1e{"' + snowman + b'": [1, 2]}\n'
    ctx, _ = seq_request(body)

    with ctx:
        assert list(flask.request.iter_json_seq()) == ["\u2603", {"\u2603": [1, 2]}]


def test_oversized_record_is_discard_with_bounded_memory(seq_request):
    body = b"\x1e" + b"x" * 10 + b"\x1e1\n"
    ctx, _ = seq_request(body)
    errors = []

    with ctx:
        records = flask.request.iter_json_seq(
            strict=False,
            on_bad_record=lambda *args: errors.append(args),
            max_record_size=5,
        )
        assert list(records) == [1]

    assert errors == [(0, (1, 11), TOO_LARGE_ERROR)]


def test_strict_mode_raises_first_bad_record(seq_request):
    body = b"\x1eok\x1e1\n"
    ctx, _ = seq_request(body)

    with ctx:
        with pytest.raises(flask.json_seq.JSONSequenceError) as exc_info:
            list(flask.request.iter_json_seq())

    assert exc_info.value.category == SYNTAX_ERROR
    assert exc_info.value.index == 0


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("get_data", {}),
        ("get_json", {"force": True, "cache": False}),
        ("iter_json_seq", {}),
    ],
)
def test_body_consumers_conflict(seq_request, method, kwargs):
    body = b'\x1e{}\n\x1e{}\n'
    ctx, _ = seq_request(body)

    with ctx:
        records = flask.request.iter_json_seq()
        next(records)

        with pytest.raises(RuntimeError):
            getattr(flask.request, method)(**kwargs)


def test_get_json_before_json_sequence_conflicts(seq_request):
    body = b'\x1e{}\n'
    ctx, _ = seq_request(body)

    with ctx:
        flask.request.get_data()

        with pytest.raises(RuntimeError):
            flask.request.iter_json_seq()


def test_plain_json_keeps_working(app, client):
    @app.post("/json")
    def json_view():
        return flask.jsonify(flask.request.get_json())

    response = client.post("/json", json={"ok": True})

    assert response.get_json() == {"ok": True}
