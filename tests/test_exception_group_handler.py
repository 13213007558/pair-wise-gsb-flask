import sys

import pytest

from flask import Blueprint

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Exception groups require Python 3.11"
)


class AppError(Exception):
    pass


class DatabaseError(AppError):
    pass


class NetworkError(AppError):
    pass


class AmbiguousError(DatabaseError, NetworkError):
    pass


def test_group_handler_basic(app, client):
    seen = []
    responses = []

    @app.group_errorhandler(DatabaseError)
    def handle_db_group(group):
        seen.append(group)
        return "database group", 500

    @app.after_request
    def count_responses(response):
        responses.append(response)
        return response

    @app.route("/")
    def index():
        raise ExceptionGroup("db", [DatabaseError("a"), DatabaseError("b")])  # noqa: F821

    response = client.get("/")
    assert response.status_code == 500
    assert response.data == b"database group"
    assert len(seen) == 1
    assert isinstance(seen[0], ExceptionGroup)  # noqa: F821
    assert len(seen[0].exceptions) == 2
    # The request produces exactly one response.
    assert len(responses) == 1


def test_group_handler_receives_original_nested_group(app, client):
    seen = []
    raised = ExceptionGroup(  # noqa: F821
        "outer",
        [
            ExceptionGroup("inner", [DatabaseError("a"), NetworkError("b")]),  # noqa: F821
            DatabaseError("c"),
        ],
    )

    @app.group_errorhandler(AppError)
    def handle_app_group(group):
        seen.append(group)
        return "app group", 500

    @app.route("/")
    def index():
        raise raised

    response = client.get("/")
    assert response.status_code == 500
    assert response.data == b"app group"
    # The handler receives the original group object, with the nested
    # tree and traceback intact, not a rebuilt or split group.
    assert seen[0] is raised
    assert isinstance(seen[0].exceptions[0], ExceptionGroup)  # noqa: F821
    assert seen[0].__traceback__ is not None


def test_group_handler_requires_all_leaves(app, client):
    called = False

    @app.group_errorhandler(DatabaseError)
    def handle_db_group(group):
        nonlocal called
        called = True
        return "database group", 500

    @app.route("/")
    def index():
        raise ExceptionGroup("mixed", [DatabaseError("a"), NetworkError("b")])  # noqa: F821

    # No group handler matches every leaf, so the group follows the
    # regular unhandled-error path and becomes a 500.
    app.config["PROPAGATE_EXCEPTIONS"] = False
    response = client.get("/")
    assert response.status_code == 500
    assert response.data != b"database group"
    assert not called


def test_unmatched_group_is_logged(app, client, caplog):
    @app.route("/")
    def index():
        raise ExceptionGroup("db", [DatabaseError("a"), DatabaseError("b")])  # noqa: F821

    app.config["PROPAGATE_EXCEPTIONS"] = False

    with caplog.at_level("ERROR", logger=app.logger.name):
        response = client.get("/")

    assert response.status_code == 500
    assert any(
        "Exception on / [GET]" in record.getMessage()
        and record.exc_info
        and isinstance(record.exc_info[1], ExceptionGroup)  # noqa: F821
        for record in caplog.records
    )


def test_group_with_base_exception_leaf_not_handled(app, client):
    @app.group_errorhandler(DatabaseError)
    def handle_db_group(group):  # pragma: no cover
        return "database group", 500

    group = BaseExceptionGroup("mixed", [DatabaseError("a"), KeyboardInterrupt()])  # noqa: F821

    # A group containing BaseException leaves, such as
    # KeyboardInterrupt, is never eligible for a group handler.
    assert app._find_group_error_handler(group, []) is None

    @app.route("/")
    def index():
        raise group

    # The group is not converted to an HTTP response, it propagates.
    with pytest.raises(BaseExceptionGroup):  # noqa: F821
        client.get("/")


def test_group_handler_ambiguity(app, client):
    @app.group_errorhandler(DatabaseError)
    def handle_db_group(group):  # pragma: no cover
        return "db", 500

    @app.group_errorhandler(NetworkError)
    def handle_network_group(group):  # pragma: no cover
        return "network", 500

    @app.route("/")
    def index():
        # AmbiguousError inherits from both DatabaseError and
        # NetworkError, so both handlers match every leaf and neither
        # is more specific.
        raise ExceptionGroup("amb", [AmbiguousError("x")])  # noqa: F821

    with pytest.raises(RuntimeError, match="Ambiguous exception group handlers"):
        client.get("/")


def test_group_handler_most_specific(app, client):
    @app.group_errorhandler(AppError)
    def handle_app_group(group):  # pragma: no cover
        return "app", 500

    @app.group_errorhandler(DatabaseError)
    def handle_db_group(group):
        return "database", 500

    @app.route("/")
    def index():
        raise ExceptionGroup("db", [DatabaseError("a")])  # noqa: F821

    response = client.get("/")
    assert response.data == b"database"


def test_group_handler_less_specific_partial_match(app, client):
    # Only the AppError handler matches every leaf. The more specific
    # DatabaseError handler is not eligible, so there is no ambiguity.
    @app.group_errorhandler(AppError)
    def handle_app_group(group):
        return "app", 500

    @app.group_errorhandler(DatabaseError)
    def handle_db_group(group):  # pragma: no cover
        return "database", 500

    @app.route("/")
    def index():
        raise ExceptionGroup("mixed", [DatabaseError("a"), NetworkError("b")])  # noqa: F821

    response = client.get("/")
    assert response.data == b"app"


def test_group_handler_blueprint_precedence(app, client):
    bp = Blueprint("bp", __name__)

    @bp.group_errorhandler(AppError)
    def handle_bp_group(group):
        return "blueprint", 500

    @app.group_errorhandler(DatabaseError)
    def handle_app_group(group):  # pragma: no cover
        return "app", 500

    @bp.route("/")
    def index():
        raise ExceptionGroup("db", [DatabaseError("a")])  # noqa: F821

    app.register_blueprint(bp)
    response = client.get("/")
    # The blueprint level is considered before the app level, even
    # though the app handler is registered for a more specific class.
    assert response.data == b"blueprint"


def test_group_handler_nested_blueprint(app, client):
    parent = Blueprint("parent", __name__)
    child = Blueprint("child", __name__)

    @child.group_errorhandler(DatabaseError)
    def handle_child_group(group):
        return "child", 500

    @parent.group_errorhandler(AppError)
    def handle_parent_group(group):  # pragma: no cover
        return "parent", 500

    @app.group_errorhandler(AppError)
    def handle_app_group(group):  # pragma: no cover
        return "app", 500

    @child.route("/")
    def index():
        raise ExceptionGroup("db", [DatabaseError("a")])  # noqa: F821

    parent.register_blueprint(child)
    app.register_blueprint(parent)
    response = client.get("/")
    assert response.data == b"child"


def test_app_group_errorhandler(app, client):
    bp = Blueprint("bp", __name__)

    @bp.app_group_errorhandler(DatabaseError)
    def handle_db_group(group):
        return "app-wide", 500

    @app.route("/")
    def index():
        raise ExceptionGroup("db", [DatabaseError("a")])  # noqa: F821

    app.register_blueprint(bp)
    response = client.get("/")
    assert response.data == b"app-wide"


def test_group_handler_async(app, client):
    pytest.importorskip("asgiref")

    @app.group_errorhandler(DatabaseError)
    async def handle_db_group(group):
        return "async database group", 500

    @app.route("/")
    async def index():
        raise ExceptionGroup("db", [DatabaseError("a")])  # noqa: F821

    response = client.get("/")
    assert response.status_code == 500
    assert response.data == b"async database group"


def test_normal_errorhandler_does_not_unwrap_group(app, client):
    called = False

    @app.errorhandler(DatabaseError)
    def handle_db(error):
        nonlocal called
        called = True
        return "single", 500

    @app.route("/")
    def index():
        raise ExceptionGroup("db", [DatabaseError("a"), DatabaseError("b")])  # noqa: F821

    # A normal error handler for the leaf class is not called for a
    # group, the group follows the regular unhandled-error path.
    app.config["PROPAGATE_EXCEPTIONS"] = False
    response = client.get("/")
    assert response.status_code == 500
    assert response.data != b"single"
    assert not called


def test_normal_errorhandler_for_exception_still_matches_group(app, client):
    @app.errorhandler(Exception)
    def handle_exc(error):
        return "generic", 500

    @app.route("/")
    def index():
        raise ExceptionGroup("db", [DatabaseError("a")])  # noqa: F821

    # Existing behavior is unchanged: a catch-all Exception handler
    # still matches a group if no group handler does.
    response = client.get("/")
    assert response.data == b"generic"


def test_group_handler_preferred_over_normal_handler(app, client):
    @app.errorhandler(Exception)
    def handle_exc(error):  # pragma: no cover
        return "generic", 500

    @app.group_errorhandler(DatabaseError)
    def handle_db_group(group):
        return "group", 500

    @app.route("/")
    def index():
        raise ExceptionGroup("db", [DatabaseError("a")])  # noqa: F821

    response = client.get("/")
    assert response.data == b"group"


def test_group_handler_registration_validation(app):
    with pytest.raises(TypeError, match="is an instance, not a class"):
        app.register_group_error_handler(DatabaseError(), None)

    with pytest.raises(ValueError, match="not a subclass of Exception"):
        app.register_group_error_handler(list, None)

    with pytest.raises(ValueError, match="not a subclass of Exception"):
        app.register_group_error_handler(KeyboardInterrupt, None)

    with pytest.raises(ValueError, match="exception group class"):
        app.register_group_error_handler(ExceptionGroup, None)  # noqa: F821


def test_group_handler_requires_python_311(app, monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 10, 0))

    with pytest.raises(RuntimeError, match="Python 3.11"):
        app.register_group_error_handler(DatabaseError, lambda group: "")
