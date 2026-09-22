import builtins
import logging
import sys

import pytest

import flask
from flask.sansio.app import AmbiguousGroupHandlerError

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="exception groups require Python 3.11"
)

# Python < 3.11 does not have exception groups, the tests are skipped there.
ExceptionGroup = getattr(builtins, "ExceptionGroup", None)
BaseExceptionGroup = getattr(builtins, "BaseExceptionGroup", None)


class LeafError(Exception):
    pass


class OtherError(Exception):
    pass


def make_leaf(exc_class=LeafError):
    try:
        raise exc_class("inner")
    except exc_class as e:
        return e


def test_group_handler_receives_original_group(app, client):
    seen = []

    @app.error_group_handler(LeafError)
    def handle_group(group):
        seen.append(group)
        return "handled", 500

    @app.route("/")
    def index():
        inner = ExceptionGroup("inner", [make_leaf()])
        raise ExceptionGroup("outer", [inner, make_leaf()])

    response = client.get("/")

    assert response.status_code == 500
    assert response.data == b"handled"
    # one request produces one response from one handler call
    assert len(seen) == 1
    group = seen[0]
    # the original tree is passed through, not flattened
    assert isinstance(group, ExceptionGroup)
    assert isinstance(group.exceptions[0], ExceptionGroup)
    assert isinstance(group.exceptions[0].exceptions[0], LeafError)
    assert isinstance(group.exceptions[1], LeafError)
    # tracebacks are preserved
    assert group.__traceback__ is not None
    assert group.exceptions[0].exceptions[0].__traceback__ is not None
    assert group.exceptions[1].__traceback__ is not None


def test_handler_matches_common_leaf_base(app, client):
    @app.error_group_handler(Exception)
    def handle_group(group):
        return "handled"

    @app.route("/")
    def index():
        raise ExceptionGroup("boom", [LeafError(), OtherError()])

    assert client.get("/").data == b"handled"


def test_unmatched_group_uses_500_and_logs(app, client, caplog):
    @app.error_group_handler(LeafError)
    def handle_group(group):
        return "handled"

    @app.route("/")
    def index():
        raise ExceptionGroup("boom", [LeafError(), OtherError()])

    app.testing = False

    with caplog.at_level(logging.ERROR, logger=app.logger.name):
        response = client.get("/")

    assert response.status_code == 500
    assert response.data != b"handled"
    assert any("Exception on /" in record.getMessage() for record in caplog.records)


def test_most_specific_leaf_class_wins(app, client):
    @app.error_group_handler(Exception)
    def handle_any(group):
        return "broad"

    @app.error_group_handler(LeafError)
    def handle_leaf(group):
        return "specific"

    @app.route("/")
    def index():
        raise ExceptionGroup("boom", [LeafError(), LeafError()])

    assert client.get("/").data == b"specific"


def test_blueprint_level_wins_over_specificity(app, client):
    bp = flask.Blueprint("bp", __name__)

    @bp.error_group_handler(Exception)
    def handle_bp(group):
        return "blueprint"

    @app.error_group_handler(LeafError)
    def handle_app(group):
        return "app"

    @bp.route("/")
    def index():
        raise ExceptionGroup("boom", [LeafError()])

    app.register_blueprint(bp)

    assert client.get("/").data == b"blueprint"


def test_nested_blueprint_levels(app, client):
    class BaseError(Exception):
        pass

    class SubError(BaseError):
        pass

    parent = flask.Blueprint("parent", __name__)
    child = flask.Blueprint("child", __name__)

    @parent.error_group_handler(SubError)
    def handle_parent(group):
        return "parent"

    @child.error_group_handler(BaseError)
    def handle_child(group):
        return "child"

    @child.route("/")
    def index():
        raise ExceptionGroup("boom", [SubError()])

    parent.register_blueprint(child)
    app.register_blueprint(parent)

    assert client.get("/").data == b"child"


def test_app_error_group_handler(app, client):
    bp = flask.Blueprint("bp", __name__)

    @bp.app_error_group_handler(LeafError)
    def handle_group(group):
        return "app-wide"

    @app.route("/")
    def index():
        raise ExceptionGroup("boom", [LeafError()])

    app.register_blueprint(bp)

    assert client.get("/").data == b"app-wide"


def test_ambiguous_group_handlers(app, client):
    class A(Exception):
        pass

    class B(Exception):
        pass

    class Both(A, B):
        pass

    @app.error_group_handler(A)
    def handle_a(group):
        return "a"

    @app.error_group_handler(B)
    def handle_b(group):
        return "b"

    @app.route("/")
    def index():
        raise ExceptionGroup("boom", [Both()])

    with pytest.raises(AmbiguousGroupHandlerError):
        client.get("/")


def test_ambiguity_resolved_by_more_specific_handler(app, client):
    class A(Exception):
        pass

    class B(Exception):
        pass

    class Both(A, B):
        pass

    @app.error_group_handler(A)
    def handle_a(group):
        return "a"

    @app.error_group_handler(B)
    def handle_b(group):
        return "b"

    @app.error_group_handler(Both)
    def handle_both(group):
        return "both"

    @app.route("/")
    def index():
        raise ExceptionGroup("boom", [Both()])

    assert client.get("/").data == b"both"


def test_group_not_split_to_plain_handlers(app, client):
    called = []

    @app.errorhandler(LeafError)
    def handle_leaf(e):
        called.append(e)
        return "leaf"

    @app.route("/")
    def index():
        raise ExceptionGroup("boom", [LeafError(), LeafError()])

    app.testing = False
    response = client.get("/")

    assert response.status_code == 500
    assert called == []


def test_plain_handler_for_group_still_works(app, client):
    @app.errorhandler(Exception)
    def handle_any(e):
        return "plain"

    @app.route("/")
    def index():
        raise ExceptionGroup("boom", [LeafError()])

    assert client.get("/").data == b"plain"


def test_group_handler_preferred_over_plain_handler(app, client):
    @app.errorhandler(Exception)
    def handle_any(e):
        return "plain"

    @app.error_group_handler(LeafError)
    def handle_group(group):
        return "group"

    @app.route("/")
    def index():
        raise ExceptionGroup("boom", [LeafError()])

    assert client.get("/").data == b"group"


def test_base_exception_leaves_not_handled(app, client):
    @app.error_group_handler(BaseException)
    def handle_group(group):
        return "handled"

    @app.route("/")
    def index():
        raise BaseExceptionGroup("boom", [KeyboardInterrupt()])

    app.testing = False

    with pytest.raises(BaseExceptionGroup):
        client.get("/")


def test_async_view_and_handler(app, client):
    pytest.importorskip("asgiref")

    @app.error_group_handler(LeafError)
    async def handle_group(group):
        return f"handled {len(group.exceptions)}"

    @app.route("/")
    async def index():
        raise ExceptionGroup("boom", [LeafError(), LeafError()])

    assert client.get("/").data == b"handled 2"


def test_register_validation(app):
    with pytest.raises(TypeError, match="is an instance, not a class"):
        app.register_error_group_handler(LeafError(), None)

    with pytest.raises(ValueError, match="is not an exception class"):
        app.register_error_group_handler(list, None)


def test_register_requires_python_311(app, monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 10, 0))

    with pytest.raises(RuntimeError, match="Python 3.11"):
        app.register_error_group_handler(LeafError, lambda group: None)
