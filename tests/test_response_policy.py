from __future__ import annotations

import logging

import pytest
from werkzeug.exceptions import NotFound

import flask
from flask import Blueprint
from flask import Flask
from flask import ResponsePolicyContext


class WSGIRecorder:
    """Record the final WSGI ``start_response`` calls and body iteration."""

    def __init__(self, app: Flask, path: str = "/", method: str = "GET"):
        self.app = app
        self.path = path
        self.method = method
        self.events: list[tuple] = []

    def start_response(self, status, headers, exc_info=None):
        self.events.append(
            ("start_response", status, list(headers), exc_info is not None)
        )

        def write(data):
            self.events.append(("write", data))

        return write

    def run(self):
        with self.app.test_request_context(self.path, method=self.method) as ctx:
            environ = ctx.request.environ

        app_iter = self.app(environ, self.start_response)
        chunks: list[bytes] = []

        try:
            for chunk in app_iter:
                chunks.append(chunk)
                self.events.append(("chunk", chunk))
        except BaseException as error:
            self.events.append(("error", type(error).__name__))
            raise
        finally:
            close = getattr(app_iter, "close", None)

            if close is not None:
                close()
                self.events.append(("closed", None))

        return chunks

    @property
    def starts(self):
        return [event for event in self.events if event[0] == "start_response"]


def test_disabled_by_default(app, client):
    @app.route("/")
    def index():
        return "ok"

    response = client.get("/")
    assert "Content-Security-Policy" not in response.headers
    assert app.response_policy_funcs == {}


def test_policy_on_normal_response(app, client):
    @app.response_policy
    def policy(ctx: ResponsePolicyContext):
        assert ctx.error is None
        assert ctx.endpoint == "index"
        assert ctx.status_code == 200
        ctx.response.headers["Content-Security-Policy"] = "default-src 'self'"
        ctx.response.headers["Referrer-Policy"] = "no-referrer"

    @app.route("/")
    def index():
        return "ok"

    response = client.get("/")
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_policy_runs_once(app, client):
    calls = []

    @app.response_policy
    def policy(ctx):
        calls.append(ctx.endpoint)

    @app.route("/")
    def index():
        return "ok"

    client.get("/")
    assert calls == ["index"]


def test_policy_endpoint_selection(app, client):
    @app.response_policy
    def policy(ctx):
        if ctx.endpoint == "admin":
            ctx.response.headers["X-Frame-Options"] = "DENY"

    @app.route("/")
    def index():
        return "ok"

    @app.route("/admin")
    def admin():
        return "admin"

    assert "X-Frame-Options" not in client.get("/").headers
    assert client.get("/admin").headers["X-Frame-Options"] == "DENY"


def test_policy_on_404_and_405(app, client):
    @app.route("/only-get", methods=["POST"])
    def only_post():
        return "ok"

    @app.response_policy
    def policy(ctx):
        ctx.response.headers["Content-Security-Policy"] = "default-src 'self'"

    not_found = client.get("/missing")
    assert not_found.status_code == 404
    assert not_found.headers["Content-Security-Policy"] == "default-src 'self'"

    method_not_allowed = client.get("/only-get")
    assert method_not_allowed.status_code == 405
    assert (
        method_not_allowed.headers["Content-Security-Policy"] == "default-src 'self'"
    )


def test_policy_on_automatic_options(app, client):
    @app.route("/only-get", methods=["POST"])
    def only_post():
        return "ok"

    @app.response_policy
    def policy(ctx):
        ctx.response.headers["Content-Security-Policy"] = "default-src 'self'"

    response = client.options("/only-get")
    assert response.status_code == 200
    assert "POST" in response.headers["Allow"]
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"


def test_policy_on_error_handler_response(app, client):
    app.testing = False

    @app.errorhandler(404)
    def handle_not_found(error):
        assert isinstance(error, NotFound)
        return "not found", 404

    @app.response_policy
    def policy(ctx):
        if ctx.status_code >= 400:
            ctx.response.headers["Content-Security-Policy"] = "default-src 'none'"

    response = client.get("/missing")
    assert response.status_code == 404
    assert response.data == b"not found"
    assert response.headers["Content-Security-Policy"] == "default-src 'none'"


def test_policy_receives_original_exception(app, client):
    app.testing = False

    class CustomError(Exception):
        pass

    seen = []

    @app.errorhandler(CustomError)
    def handle_custom(error):
        seen.append(error)
        return "custom", 500

    @app.response_policy
    def policy(ctx):
        seen.append(ctx.error)

    @app.route("/boom")
    def boom():
        raise CustomError("boom")

    response = client.get("/boom")
    assert response.status_code == 500
    assert len(seen) == 2
    assert isinstance(seen[0], CustomError)
    assert seen[1] is seen[0]


def test_policy_after_after_request_overrides_same_header(app, client):
    order = []

    @app.after_request
    def after(response):
        order.append("after_request")
        response.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
        return response

    @app.response_policy
    def policy(ctx):
        order.append("response_policy")
        ctx.response.headers["Referrer-Policy"] = "no-referrer"

    @app.route("/")
    def index():
        return "ok"

    response = client.get("/")
    assert order == ["after_request", "response_policy"]
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_policy_keeps_unrelated_session_cookie(app, client):
    app.secret_key = "secret"

    @app.route("/")
    def index():
        flask.session["x"] = 1
        return "ok"

    @app.response_policy
    def policy(ctx):
        ctx.response.headers["Referrer-Policy"] = "no-referrer"

    response = client.get("/")
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "Set-Cookie" in response.headers


def test_policy_failure_is_isolated_and_logged(app, client, caplog):
    @app.response_policy
    def good_policy(ctx):
        ctx.response.headers["X-Good"] = "1"

    @app.response_policy
    def bad_policy(ctx):
        raise ValueError("policy bug")

    @app.route("/")
    def index():
        return "ok"

    with caplog.at_level(logging.ERROR):
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["X-Good"] == "1"
    assert "Response policy 'bad_policy' failed" in caplog.text


def test_policy_runs_once_when_finalization_reentered(app, client):
    from flask import request_finished

    runs = []

    @app.route("/")
    def index():
        return "ok"

    @app.response_policy
    def policy(ctx):
        runs.append(ctx.endpoint)
        ctx.response.headers["X-Policy"] = "1"

    def crash_signal(sender, response, **kwargs):
        raise RuntimeError("signal crashed")

    request_finished.connect(crash_signal, app)

    # The signal failure re-enters finalization through handle_exception,
    # but policies must not run again on the replacement response.
    with pytest.raises(RuntimeError, match="signal crashed"):
        client.get("/")

    assert runs == ["index"]


def test_policy_runs_once_for_handled_error(app, client):
    app.testing = False
    runs = []

    @app.route("/boom")
    def boom():
        raise RuntimeError("view failed")

    @app.errorhandler(500)
    def handle_500(error):
        return "handled", 500

    @app.response_policy
    def policy(ctx):
        runs.append(type(ctx.error).__name__ if ctx.error else None)
        ctx.response.headers["X-Policy"] = "1"

    response = client.get("/boom")

    assert response.status_code == 500
    assert runs == ["RuntimeError"]
    assert response.headers.get("X-Policy") == "1"


def test_broken_error_handler_propagates_without_policy_reentry(app, client, caplog):
    app.testing = False
    app.config["PROPAGATE_EXCEPTIONS"] = False
    runs = []

    @app.route("/boom")
    def boom():
        raise RuntimeError("view failed")

    @app.errorhandler(500)
    def broken_handler(error):
        raise RuntimeError("handler failed")

    @app.response_policy
    def policy(ctx):
        runs.append(ctx.endpoint)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="handler failed"):
            client.get("/boom")

    # The policy never gets a second chance to run for a response that can
    # no longer be produced, so it cannot recurse into itself.
    assert runs == []


def test_blueprint_policy_only_applies_to_blueprint(app, client):
    bp = Blueprint("bp", __name__)

    @bp.route("/bp-view")
    def bp_view():
        return "bp"

    @bp.response_policy
    def bp_policy(ctx):
        ctx.response.headers["X-Bp"] = "1"

    @app.response_policy
    def app_policy(ctx):
        ctx.response.headers["X-App"] = "1"

    @app.route("/app-view")
    def app_view():
        return "app"

    app.register_blueprint(bp, url_prefix="/b")

    bp_response = client.get("/b/bp-view")
    assert bp_response.headers["X-Bp"] == "1"
    assert bp_response.headers["X-App"] == "1"

    app_response = client.get("/app-view")
    assert "X-Bp" not in app_response.headers
    assert app_response.headers["X-App"] == "1"


def test_blueprint_policy_runs_before_app_policy(app, client):
    bp = Blueprint("bp", __name__)
    order = []

    @bp.route("/view")
    def view():
        return "ok"

    @bp.response_policy
    def bp_policy(ctx):
        order.append("bp")
        ctx.response.headers["X-Order"] = "bp"

    @app.response_policy
    def app_policy(ctx):
        order.append("app")
        ctx.response.headers["X-Order"] = "app"

    app.register_blueprint(bp, url_prefix="/b")

    response = client.get("/b/view")
    assert order == ["bp", "app"]
    assert response.headers["X-Order"] == "app"


def test_app_response_policy_on_blueprint(app, client):
    bp = Blueprint("bp", __name__)

    @bp.route("/view")
    def view():
        return "ok"

    @bp.app_response_policy
    def global_policy(ctx):
        ctx.response.headers["X-Global"] = "1"

    @app.route("/outside")
    def outside():
        return "ok"

    app.register_blueprint(bp, url_prefix="/b")

    assert client.get("/b/view").headers["X-Global"] == "1"
    assert client.get("/outside").headers["X-Global"] == "1"


def test_async_response_policy(app, client):
    pytest.importorskip("asgiref")

    @app.response_policy
    async def policy(ctx):
        ctx.response.headers["X-Async"] = "1"

    @app.route("/")
    def index():
        return "ok"

    response = client.get("/")
    assert response.headers["X-Async"] == "1"


def test_stream_failure_after_headers_committed(app):
    @app.route("/stream")
    def stream():
        def generate():
            yield b"chunk1\n"
            raise RuntimeError("stream failed")

        return generate()

    @app.response_policy
    def policy(ctx):
        ctx.response.headers["Content-Security-Policy"] = "default-src 'self'"

    recorder = WSGIRecorder(app, "/stream")

    with pytest.raises(RuntimeError, match="stream failed"):
        recorder.run()

    assert len(recorder.starts) == 1
    status, headers, used_exc_info = recorder.starts[0][1:]
    assert status == "200 OK"
    assert ("Content-Security-Policy", "default-src 'self'") in headers
    assert used_exc_info is False
    assert [event[0] for event in recorder.events] == [
        "start_response",
        "chunk",
        "error",
        "closed",
    ]


def test_stream_boundary_logged(app, caplog):
    @app.route("/stream")
    def stream():
        def generate():
            yield b"chunk1\n"
            raise RuntimeError("stream failed")

        return generate()

    @app.response_policy
    def policy(ctx):
        ctx.response.headers["Referrer-Policy"] = "no-referrer"

    recorder = WSGIRecorder(app, "/stream")

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError):
            recorder.run()

    assert "headers were committed" in caplog.text
    assert "cannot add or change headers" in caplog.text


def test_stream_closed_early(app):
    closed = []

    @app.route("/stream")
    def stream():
        def generate():
            try:
                yield b"chunk1\n"
                yield b"chunk2\n"
            finally:
                closed.append(True)

        return generate()

    @app.response_policy
    def policy(ctx):
        ctx.response.headers["Referrer-Policy"] = "no-referrer"

    with app.test_request_context("/stream") as ctx:
        environ = ctx.request.environ

    def ignore_start_response(status, headers, exc_info=None):
        return None

    app_iter = app(environ, ignore_start_response)
    iterator = iter(app_iter)
    assert next(iterator) == b"chunk1\n"
    app_iter.close()

    assert closed == [True]


def test_normal_stream_iteration_has_no_boundary_log(app, caplog):
    @app.route("/stream")
    def stream():
        def generate():
            yield b"chunk1\n"
            yield b"chunk2\n"

        return generate()

    @app.response_policy
    def policy(ctx):
        ctx.response.headers["Referrer-Policy"] = "no-referrer"

    recorder = WSGIRecorder(app, "/stream")

    with caplog.at_level(logging.ERROR):
        chunks = recorder.run()

    assert chunks == [b"chunk1\n", b"chunk2\n"]
    assert caplog.text == ""
    assert len(recorder.starts) == 1


def test_no_wrapper_when_policy_disabled(app):
    from flask.security import _PolicyResponseIterator

    @app.route("/")
    def index():
        return "ok"

    with app.test_request_context("/") as ctx:
        environ = ctx.request.environ

    def ignore_start_response(status, headers, exc_info=None):
        return None

    result = app(environ, ignore_start_response)
    assert not isinstance(result, _PolicyResponseIterator)
