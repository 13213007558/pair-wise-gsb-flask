import json
import os
from copy import deepcopy

import pytest

import flask

# config keys used for the TestConfig
TEST_KEY = "foo"
SECRET_KEY = "config"


def common_object_test(app):
    assert app.secret_key == "config"
    assert app.config["TEST_KEY"] == "foo"
    assert "TestConfig" not in app.config


def test_config_from_pyfile():
    app = flask.Flask(__name__)
    app.config.from_pyfile(f"{__file__.rsplit('.', 1)[0]}.py")
    common_object_test(app)


def test_config_from_object():
    app = flask.Flask(__name__)
    app.config.from_object(__name__)
    common_object_test(app)


def test_config_from_file_json():
    app = flask.Flask(__name__)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app.config.from_file(os.path.join(current_dir, "static", "config.json"), json.load)
    common_object_test(app)


def test_config_from_file_toml():
    tomllib = pytest.importorskip("tomllib", reason="tomllib added in 3.11")
    app = flask.Flask(__name__)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app.config.from_file(
        os.path.join(current_dir, "static", "config.toml"), tomllib.load, text=False
    )
    common_object_test(app)


def test_from_prefixed_env(monkeypatch):
    monkeypatch.setenv("FLASK_STRING", "value")
    monkeypatch.setenv("FLASK_BOOL", "true")
    monkeypatch.setenv("FLASK_INT", "1")
    monkeypatch.setenv("FLASK_FLOAT", "1.2")
    monkeypatch.setenv("FLASK_LIST", "[1, 2]")
    monkeypatch.setenv("FLASK_DICT", '{"k": "v"}')
    monkeypatch.setenv("NOT_FLASK_OTHER", "other")

    app = flask.Flask(__name__)
    app.config.from_prefixed_env()

    assert app.config["STRING"] == "value"
    assert app.config["BOOL"] is True
    assert app.config["INT"] == 1
    assert app.config["FLOAT"] == 1.2
    assert app.config["LIST"] == [1, 2]
    assert app.config["DICT"] == {"k": "v"}
    assert "OTHER" not in app.config


def test_from_prefixed_env_custom_prefix(monkeypatch):
    monkeypatch.setenv("FLASK_A", "a")
    monkeypatch.setenv("NOT_FLASK_A", "b")

    app = flask.Flask(__name__)
    app.config.from_prefixed_env("NOT_FLASK")

    assert app.config["A"] == "b"


def test_from_prefixed_env_nested(monkeypatch):
    monkeypatch.setenv("FLASK_EXIST__ok", "other")
    monkeypatch.setenv("FLASK_EXIST__inner__ik", "2")
    monkeypatch.setenv("FLASK_EXIST__new__more", '{"k": false}')
    monkeypatch.setenv("FLASK_NEW__K", "v")

    app = flask.Flask(__name__)
    app.config["EXIST"] = {"ok": "value", "flag": True, "inner": {"ik": 1}}
    app.config.from_prefixed_env()

    if os.name != "nt":
        assert app.config["EXIST"] == {
            "ok": "other",
            "flag": True,
            "inner": {"ik": 2},
            "new": {"more": {"k": False}},
        }
    else:
        # Windows env var keys are always uppercase.
        assert app.config["EXIST"] == {
            "ok": "value",
            "OK": "other",
            "flag": True,
            "inner": {"ik": 1},
            "INNER": {"IK": 2},
            "NEW": {"MORE": {"k": False}},
        }

    assert app.config["NEW"] == {"K": "v"}


def test_config_from_mapping():
    app = flask.Flask(__name__)
    app.config.from_mapping({"SECRET_KEY": "config", "TEST_KEY": "foo"})
    common_object_test(app)

    app = flask.Flask(__name__)
    app.config.from_mapping([("SECRET_KEY", "config"), ("TEST_KEY", "foo")])
    common_object_test(app)

    app = flask.Flask(__name__)
    app.config.from_mapping(SECRET_KEY="config", TEST_KEY="foo")
    common_object_test(app)

    app = flask.Flask(__name__)
    app.config.from_mapping(SECRET_KEY="config", TEST_KEY="foo", skip_key="skip")
    common_object_test(app)

    app = flask.Flask(__name__)
    with pytest.raises(TypeError):
        app.config.from_mapping({}, {})


def test_config_from_layers_merge_and_resolve():
    app = flask.Flask(__name__)
    app.config.from_layers(
        (
            "base",
            {
                "DATABASE": {"HOST": "db", "PORT": 5432},
                "PORT_REF": "${DATABASE.PORT}",
                "DATABASE_REF": "${DATABASE}",
                "URL": "postgres://${DATABASE.HOST}:${DATABASE.PORT}/app",
                "SERVERS": ["base"],
                "EMPTY": None,
                "REMOVED": "old",
            },
        ),
        (
            "datacenter",
            {
                "DATABASE": {"PORT": 6543},
                "SERVERS": ["dc"],
            },
        ),
        (
            "environment",
            {
                "DATABASE": {"HOST": "db-prod"},
                "REMOVED": flask.Config.DELETE,
            },
        ),
    )

    assert app.config["DATABASE"] == {"HOST": "db-prod", "PORT": 6543}
    assert app.config["DATABASE_REF"] == {"HOST": "db-prod", "PORT": 6543}
    assert app.config["PORT_REF"] == 6543
    assert app.config["URL"] == "postgres://db-prod:6543/app"
    assert app.config["SERVERS"] == ["dc"]
    assert app.config["EMPTY"] is None
    assert "REMOVED" not in app.config

    assert app.config.source_for("DATABASE.PORT") == "datacenter"
    assert app.config.sources_for("DATABASE.PORT") == ["base", "datacenter"]
    assert app.config.source_for("DATABASE.HOST") == "environment"
    assert app.config.sources_for("DATABASE.HOST") == ["base", "environment"]
    assert app.config.source_for("URL") == "base"
    assert app.config.source_for("SERVERS") == "datacenter"
    assert app.config.source_for("SERVERS.0") == "datacenter"
    assert app.config.source_for("EMPTY") == "base"
    with pytest.raises(KeyError, match="REMOVED"):
        app.config.source_for("REMOVED")


def test_config_from_layers_escapes_dollars():
    app = flask.Flask(__name__)
    app.config.from_layers(
        (
            "base",
            {
                "ESCAPED_DOLLAR": "$$",
                "ESCAPED_REFERENCE": "\\$" + "{MISSING}",
                "BACKSLASH_LITERAL": "\\$x",
                "LITERAL": "$x",
            },
        )
    )

    assert app.config["ESCAPED_DOLLAR"] == "$"
    assert app.config["BACKSLASH_LITERAL"] == "\\$x"
    assert app.config["ESCAPED_REFERENCE"] == "$" + "{MISSING}"
    assert app.config["LITERAL"] == "$x"


def test_config_from_layers_is_atomic_and_does_not_mutate_input():
    app = flask.Config(__file__)
    app["EXISTING"] = "kept"
    layers = [
        (
            "base",
            {
                "A": "${B}",
                "B": "${A}",
                "NESTED": {"VALUES": [1, 2]},
                "REMOVED": "old",
            },
        ),
        ("environment", {"REMOVED": flask.Config.DELETE}),
    ]
    expected_base = deepcopy(layers[0][1])

    with pytest.raises(flask.ConfigReferenceError):
        app.from_layers(*layers)

    assert dict(app) == {"EXISTING": "kept"}
    assert layers[0][1] == expected_base
    assert set(layers[1][1]) == {"REMOVED"}
    assert layers[1][1]["REMOVED"] is flask.Config.DELETE


def test_config_from_layers_sensitive_diagnostics():
    app = flask.Flask(__name__)
    app.config.from_layers(
        (
            "base",
            {
                "PASSWORD": "secret",
                "TOKEN": "${PASSWORD}",
                "URL": "postgres://user:${PASSWORD}@db/app",
                "CREDENTIALS": {"PASSWORD": "secret", "NAME": "app"},
                "CREDENTIAL_URL": "login:${CREDENTIALS.PASSWORD}",
            },
        ),
        sensitive=["PASSWORD", "CREDENTIALS.PASSWORD"],
    )

    assert app.config["PASSWORD"] == "secret"
    assert app.config["TOKEN"] == "secret"
    assert app.config["URL"] == "postgres://user:secret@db/app"
    assert app.config.is_sensitive("PASSWORD")
    assert app.config.is_sensitive("TOKEN")
    assert app.config.is_sensitive("URL")
    assert app.config.is_sensitive("CREDENTIALS")
    assert app.config.is_sensitive("CREDENTIALS.PASSWORD")
    assert not app.config.is_sensitive("CREDENTIALS.NAME")
    assert app.config.is_sensitive("CREDENTIAL_URL")
    assert app.config.source_for("PASSWORD") == "base"
    assert "secret" not in repr(app.config)


def test_config_from_layers_circular_reference():
    app = flask.Flask(__name__)

    with pytest.raises(flask.ConfigReferenceError) as error:
        app.config.from_layers(
            ("base", {"A": "${B}", "B": "${A}"}),
        )

    assert error.value.path == ("B",)
    assert error.value.source_path == (("B",), "base")
    assert error.value.cycle == (
        (("A",), "base"),
        (("B",), "base"),
        (("A",), "base"),
    )
    assert "A (from base) -> B (from base) -> A (from base)" in str(error.value)


def test_config_from_layers_cycle_through_mapping_reports_full_path():
    app = flask.Flask(__name__)

    with pytest.raises(flask.ConfigReferenceError) as error:
        app.config.from_layers(
            ("base", {"A": "${GROUP.B}", "GROUP": {"B": "${A}"}}),
        )

    assert error.value.cycle == (
        (("A",), "base"),
        (("GROUP", "B"), "base"),
        (("A",), "base"),
    )
    assert "GROUP.B (from base)" in str(error.value)


def test_config_from_layers_reference_to_deleted_value():
    app = flask.Flask(__name__)

    with pytest.raises(flask.ConfigReferenceError) as error:
        app.config.from_layers(
            ("base", {"A": "value", "B": "${A}"}),
            ("environment", {"A": flask.Config.DELETE}),
        )

    assert error.value.path == ("B",)
    assert error.value.deleted_layer == "environment"
    assert "B (from environment" not in str(error.value)
    assert "B (from base)" in str(error.value)
    assert "deleted by layer 'environment'" in str(error.value)


def test_config_from_layers_override_breaks_cycle():
    app = flask.Flask(__name__)
    app.config.from_layers(
        ("base", {"A": "${B}", "B": "${A}"}),
        ("environment", {"B": "resolved"}),
    )

    assert app.config["A"] == "resolved"
    assert app.config["B"] == "resolved"


def test_config_from_layers_delete_and_recreate_tracks_values():
    app = flask.Flask(__name__)
    app.config.from_layers(
        (
            "base",
            {"VALUE": "old", "MAPPING": {"KEEP": 1, "OLD": 2}, "LIST": [1, 2]},
        ),
        (
            "datacenter",
            {
                "VALUE": flask.Config.DELETE,
                "MAPPING": {"OLD": flask.Config.DELETE},
                "LIST": [3],
            },
        ),
        (
            "environment",
            {
                "VALUE": "new",
                "MAPPING": {"KEEP": 10, "NEW": 3},
            },
        ),
    )

    assert app.config["VALUE"] == "new"
    assert app.config["MAPPING"] == {"KEEP": 10, "NEW": 3}
    assert app.config["LIST"] == [3]
    assert app.config.sources_for("VALUE") == ["base", "environment"]
    assert app.config.sources_for("MAPPING.KEEP") == ["base", "environment"]
    assert app.config.sources_for("MAPPING.NEW") == ["environment"]
    assert app.config.sources_for("LIST") == ["base", "datacenter"]
    assert app.config.sources_for("LIST.0") == ["datacenter"]


def test_config_from_layers_app_factory_instance_isolation():
    shared_base = {
        "SERVICE": {"TIMEOUT": 1, "ENDPOINTS": ["base"]},
    }

    def create_alpha_app():
        app = flask.Flask(__name__)
        app.config.from_layers(
            ("base", shared_base),
            ("datacenter", {"SERVICE": {"ENDPOINTS": ["alpha"]}}),
        )
        return app

    def create_beta_app():
        app = flask.Flask(__name__)
        app.config.from_layers(
            ("base", shared_base),
            ("datacenter", {"SERVICE": {"ENDPOINTS": ["beta"]}}),
        )
        return app

    alpha = create_alpha_app()
    beta = create_beta_app()

    alpha.config["SERVICE"]["TIMEOUT"] = 30
    alpha.config["SERVICE"]["ENDPOINTS"].append("mutated")

    assert alpha.config["SERVICE"] == {
        "TIMEOUT": 30,
        "ENDPOINTS": ["alpha", "mutated"],
    }
    assert beta.config["SERVICE"] == {"TIMEOUT": 1, "ENDPOINTS": ["beta"]}
    assert shared_base == {"SERVICE": {"TIMEOUT": 1, "ENDPOINTS": ["base"]}}


def test_config_from_class():
    class Base:
        TEST_KEY = "foo"

    class Test(Base):
        SECRET_KEY = "config"

    app = flask.Flask(__name__)
    app.config.from_object(Test)
    common_object_test(app)


def test_config_from_envvar(monkeypatch):
    monkeypatch.setattr("os.environ", {})
    app = flask.Flask(__name__)

    with pytest.raises(RuntimeError) as e:
        app.config.from_envvar("FOO_SETTINGS")

    assert "'FOO_SETTINGS' is not set" in str(e.value)
    assert not app.config.from_envvar("FOO_SETTINGS", silent=True)

    monkeypatch.setattr(
        "os.environ", {"FOO_SETTINGS": f"{__file__.rsplit('.', 1)[0]}.py"}
    )
    assert app.config.from_envvar("FOO_SETTINGS")
    common_object_test(app)


def test_config_from_envvar_missing(monkeypatch):
    monkeypatch.setattr("os.environ", {"FOO_SETTINGS": "missing.cfg"})
    app = flask.Flask(__name__)
    with pytest.raises(IOError) as e:
        app.config.from_envvar("FOO_SETTINGS")
    msg = str(e.value)
    assert msg.startswith(
        "[Errno 2] Unable to load configuration file (No such file or directory):"
    )
    assert msg.endswith("missing.cfg'")
    assert not app.config.from_envvar("FOO_SETTINGS", silent=True)


def test_config_missing():
    app = flask.Flask(__name__)
    with pytest.raises(IOError) as e:
        app.config.from_pyfile("missing.cfg")
    msg = str(e.value)
    assert msg.startswith(
        "[Errno 2] Unable to load configuration file (No such file or directory):"
    )
    assert msg.endswith("missing.cfg'")
    assert not app.config.from_pyfile("missing.cfg", silent=True)


def test_config_missing_file():
    app = flask.Flask(__name__)
    with pytest.raises(IOError) as e:
        app.config.from_file("missing.json", load=json.load)
    msg = str(e.value)
    assert msg.startswith(
        "[Errno 2] Unable to load configuration file (No such file or directory):"
    )
    assert msg.endswith("missing.json'")
    assert not app.config.from_file("missing.json", load=json.load, silent=True)


def test_custom_config_class():
    class Config(flask.Config):
        pass

    class Flask(flask.Flask):
        config_class = Config

    app = Flask(__name__)
    assert isinstance(app.config, Config)
    app.config.from_object(__name__)
    common_object_test(app)


def test_session_lifetime():
    app = flask.Flask(__name__)
    app.config["PERMANENT_SESSION_LIFETIME"] = 42
    assert app.permanent_session_lifetime.seconds == 42


def test_get_namespace():
    app = flask.Flask(__name__)
    app.config["FOO_OPTION_1"] = "foo option 1"
    app.config["FOO_OPTION_2"] = "foo option 2"
    app.config["BAR_STUFF_1"] = "bar stuff 1"
    app.config["BAR_STUFF_2"] = "bar stuff 2"
    foo_options = app.config.get_namespace("FOO_")
    assert 2 == len(foo_options)
    assert "foo option 1" == foo_options["option_1"]
    assert "foo option 2" == foo_options["option_2"]
    bar_options = app.config.get_namespace("BAR_", lowercase=False)
    assert 2 == len(bar_options)
    assert "bar stuff 1" == bar_options["STUFF_1"]
    assert "bar stuff 2" == bar_options["STUFF_2"]
    foo_options = app.config.get_namespace("FOO_", trim_namespace=False)
    assert 2 == len(foo_options)
    assert "foo option 1" == foo_options["foo_option_1"]
    assert "foo option 2" == foo_options["foo_option_2"]
    bar_options = app.config.get_namespace(
        "BAR_", lowercase=False, trim_namespace=False
    )
    assert 2 == len(bar_options)
    assert "bar stuff 1" == bar_options["BAR_STUFF_1"]
    assert "bar stuff 2" == bar_options["BAR_STUFF_2"]


@pytest.mark.parametrize("encoding", ["utf-8", "iso-8859-15", "latin-1"])
def test_from_pyfile_weird_encoding(tmp_path, encoding):
    f = tmp_path / "my_config.py"
    f.write_text(f'# -*- coding: {encoding} -*-\nTEST_VALUE = "föö"\n', encoding)
    app = flask.Flask(__name__)
    app.config.from_pyfile(os.fspath(f))
    value = app.config["TEST_VALUE"]
    assert value == "föö"
