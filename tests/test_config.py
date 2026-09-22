import copy
import json
import os

import pytest

import flask
from flask.config import CircularConfigReference
from flask.config import ConfigLayer
from flask.config import InvalidConfigReference
from flask.config import MissingConfigReference

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


def test_config_from_layers_merge_references_and_provenance():
    base = {
        "DATABASE": {"HOST": "db", "PORT": 5432},
        "DB_PORT": "${DATABASE.PORT}",
        "DB_URL": "postgres://db:${DATABASE.PORT}/app",
        "LIST": [1, 2],
        "NESTED_LIST": {"ITEMS": [1]},
        "LIST_ITEM": "${LIST.0}",
        "ESCAPED": "$$ literal $x ${DATABASE.HOST}",
        "PASSWORD": "secret",
        "DERIVED": "password=${PASSWORD}",
    }
    room = {
        "DATABASE": {"PORT": 6543, "REGION": "cn"},
        "LIST": [3],
        "NESTED_LIST": {"ITEMS": [2], "NAME": "primary"},
    }
    env = {"DATABASE": {"HOST": "localhost"}, "REMOVED": flask.Config.DELETE}
    base_before = copy.deepcopy(base)
    room_before = copy.deepcopy(room)
    env_before = copy.deepcopy(env)

    app = flask.Flask(__name__)
    assert app.config.from_layers(
        ConfigLayer("base", base, sensitive_keys=[("PASSWORD",)]),
        ("room", room),
        ConfigLayer("env", env),
    )

    assert app.config["DATABASE"] == {
        "HOST": "localhost",
        "PORT": 6543,
        "REGION": "cn",
    }
    assert app.config["DB_PORT"] == 6543
    assert isinstance(app.config["DB_PORT"], int)
    assert app.config["DB_URL"] == "postgres://db:6543/app"
    assert app.config["LIST"] == [3]
    assert app.config["LIST_ITEM"] == 3
    assert app.config["NESTED_LIST"] == {"ITEMS": [2], "NAME": "primary"}
    assert app.config["ESCAPED"] == "$ literal $x localhost"
    assert "REMOVED" not in app.config

    port_source = app.config.get_source("DATABASE", "PORT")
    assert port_source.path == ("DATABASE", "PORT")
    assert port_source.layer == "room"
    assert port_source.overridden_layers == ("base",)
    assert app.config.get_source(("DATABASE", "HOST")).layer == "env"
    assert app.config.get_source("DATABASE.HOST").layer == "env"
    assert app.config.get_source("LIST", 0).layer == "room"
    assert app.config.get_source("LIST_ITEM").layer == "room"
    assert app.config.get_source("DB_PORT").layer == "room"
    assert app.config.get_source("DB_PORT").overridden_layers == ("base",)
    assert app.config.get_source("DB_URL").layer == "base"

    alias = flask.Flask(__name__)
    alias.config.from_layers(
        ("base", {"TARGET": {"X": 1, "Y": 2}, "ALIAS": "${TARGET}"}),
        ("room", {"TARGET": {"X": 3}}),
    )
    assert alias.config["ALIAS"] == {"X": 3, "Y": 2}
    alias_source = alias.config.get_source("ALIAS", "X")
    assert alias_source.layer == "room"
    assert alias_source.overridden_layers == ("base",)

    password_source = app.config.get_source("PASSWORD")
    derived_source = app.config.get_source("DERIVED")
    assert password_source.sensitive
    assert derived_source.sensitive
    assert str(password_source) == "PASSWORD from base"
    assert "secret" not in repr(password_source)
    assert "secret" not in str(derived_source)

    assert base == base_before
    assert room == room_before
    assert env == env_before


def test_config_from_layers_app_factory_isolation():
    shared_base = {
        "DATABASE": {"PORT": 5432, "REPLICAS": [{"PORT": 5433}]},
        "PORT_REF": "${DATABASE.PORT}",
        "REPLICA_REF": "${DATABASE.REPLICAS}",
    }

    def create_app(port):
        app = flask.Flask(__name__)
        app.config.from_layers(
            ConfigLayer("base", copy.deepcopy(shared_base)),
            ("environment", {"DATABASE": {"PORT": port}}),
        )
        return app

    first = create_app(6543)
    second = create_app(7654)

    assert first.config["DATABASE"]["PORT"] == 6543
    assert second.config["DATABASE"]["PORT"] == 7654
    assert first.config["PORT_REF"] == 6543
    assert second.config["PORT_REF"] == 7654

    first.config["DATABASE"]["PORT"] = 1
    first.config["DATABASE"]["REPLICAS"][0]["PORT"] = 2
    first.config["REPLICA_REF"].append({"PORT": 3})

    assert second.config["DATABASE"]["PORT"] == 7654
    assert second.config["DATABASE"]["REPLICAS"] == [{"PORT": 5433}]
    assert second.config["REPLICA_REF"] == [{"PORT": 5433}]
    assert first.config.get_source("DATABASE", "PORT").layer == "environment"
    assert second.config.get_source("DATABASE", "PORT").layer == "environment"


def test_config_from_layers_reports_circular_reference():
    with pytest.raises(CircularConfigReference) as error:
        flask.Flask(__name__).config.from_layers(
            ConfigLayer(
                "base",
                {"A": "${B}", "B": "${A}"},
            )
        )

    assert error.value.path == ("B",)
    assert error.value.layer == "base"
    assert "A (base) -> B (base) -> A (base)" in str(error.value)


def test_config_from_layers_reports_deleted_reference():
    with pytest.raises(MissingConfigReference) as error:
        flask.Flask(__name__).config.from_layers(
            ConfigLayer("base", {"REMOVED": "value"}),
            ConfigLayer(
                "region",
                {
                    "REMOVED": flask.Config.DELETE,
                    "DERIVED": "${REMOVED}",
                },
            ),
        )

    assert error.value.path == ("DERIVED",)
    assert error.value.layer == "region"
    assert error.value.reference_path == ("REMOVED",)
    assert error.value.deleted_by == "region"
    message = str(error.value)
    assert "REMOVED" in message
    assert "DERIVED" in message
    assert "region" in message
    assert "value" not in message


def test_config_from_layers_reports_deleted_parent_reference():
    with pytest.raises(MissingConfigReference) as error:
        flask.Flask(__name__).config.from_layers(
            ("base", {"DATABASE": {"PORT": 5432}}),
            (
                "environment",
                {"DATABASE": flask.Config.DELETE, "PORT": "\u0024{DATABASE.PORT}"},
            ),
        )

    assert error.value.path == ("PORT",)
    assert error.value.layer == "environment"
    assert error.value.reference_path == ("DATABASE", "PORT")
    assert error.value.deleted_by == "environment"


def test_config_from_layers_override_breaks_cycle():
    app = flask.Flask(__name__)
    app.config.from_layers(
        ConfigLayer(
            "base",
            {"A": "${B}", "B": "${A}"},
        ),
        ConfigLayer("override", {"B": 7}),
    )

    assert app.config["A"] == 7
    assert app.config["B"] == 7
    assert app.config.get_source("A").layer == "override"
    assert app.config.get_source("B").layer == "override"


def test_config_from_layers_failure_is_atomic():
    app = flask.Flask(__name__)
    app.config["EXISTING"] = {"KEEP": True}
    original = copy.deepcopy(dict(app.config))
    base = {
        "DATABASE": {"PORT": 5432},
        "PORT": "${MISSING}",
        "LIST": [1, 2],
    }
    room = {"DATABASE": {"HOST": "db"}, "LIST": [3]}
    base_before = copy.deepcopy(base)

    with pytest.raises(MissingConfigReference) as error:
        app.config.from_layers(("base", base), ("room", room))

    assert error.value.path == ("PORT",)
    assert error.value.layer == "base"
    assert dict(app.config) == original
    assert app.config._config_sources == {}
    assert base == base_before


def test_config_from_layers_no_python_or_environment_evaluation():
    with pytest.raises(MissingConfigReference):
        flask.Flask(__name__).config.from_layers(
            (
                "base",
                {"PYTHON": "${__import__('os').environ}"},
            )
        )


def test_config_from_layers_rejects_invalid_reference_syntax():
    with pytest.raises(InvalidConfigReference) as error:
        flask.Flask(__name__).config.from_layers(
            ("base", {"BAD": "\u0024{MISSING"})
        )

    assert error.value.path == ("BAD",)
    assert error.value.layer == "base"


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
