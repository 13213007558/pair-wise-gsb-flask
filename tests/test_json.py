import datetime
import decimal
import io
import uuid

import pytest
from werkzeug.http import http_date

import flask
from flask import json
from flask.json.provider import DefaultJSONProvider


@pytest.mark.parametrize("debug", (True, False))
def test_bad_request_debug_message(app, client, debug):
    app.config["DEBUG"] = debug
    app.config["TRAP_BAD_REQUEST_ERRORS"] = False

    @app.route("/json", methods=["POST"])
    def post_json():
        flask.request.get_json()
        return None

    rv = client.post("/json", data=None, content_type="application/json")
    assert rv.status_code == 400
    contains = b"Failed to decode JSON object" in rv.data
    assert contains == debug


def test_json_bad_requests(app, client):
    @app.route("/json", methods=["POST"])
    def return_json():
        return flask.jsonify(foo=str(flask.request.get_json()))

    rv = client.post("/json", data="malformed", content_type="application/json")
    assert rv.status_code == 400


def test_json_custom_mimetypes(app, client):
    @app.route("/json", methods=["POST"])
    def return_json():
        return flask.request.get_json()

    rv = client.post("/json", data='"foo"', content_type="application/x+json")
    assert rv.data == b"foo"


@pytest.mark.parametrize(
    "test_value,expected", [(True, '"\\u2603"'), (False, '"\u2603"')]
)
def test_json_as_unicode(test_value, expected, app, app_ctx):
    app.json.ensure_ascii = test_value
    rv = app.json.dumps("\N{SNOWMAN}")
    assert rv == expected


def test_json_dump_to_file(app, app_ctx):
    test_data = {"name": "Flask"}
    out = io.StringIO()

    flask.json.dump(test_data, out)
    out.seek(0)
    rv = flask.json.load(out)
    assert rv == test_data


@pytest.mark.parametrize(
    "test_value", [0, -1, 1, 23, 3.14, "s", "longer string", True, False, None]
)
def test_jsonify_basic_types(test_value, app, client):
    url = "/jsonify_basic_types"
    app.add_url_rule(url, url, lambda x=test_value: flask.jsonify(x))
    rv = client.get(url)
    assert rv.mimetype == "application/json"
    assert flask.json.loads(rv.data) == test_value


def test_jsonify_dicts(app, client):
    d = {
        "a": 0,
        "b": 23,
        "c": 3.14,
        "d": "t",
        "e": "Hi",
        "f": True,
        "g": False,
        "h": ["test list", 10, False],
        "i": {"test": "dict"},
    }

    @app.route("/kw")
    def return_kwargs():
        return flask.jsonify(**d)

    @app.route("/dict")
    def return_dict():
        return flask.jsonify(d)

    for url in "/kw", "/dict":
        rv = client.get(url)
        assert rv.mimetype == "application/json"
        assert flask.json.loads(rv.data) == d


def test_jsonify_arrays(app, client):
    """Test jsonify of lists and args unpacking."""
    a_list = [
        0,
        42,
        3.14,
        "t",
        "hello",
        True,
        False,
        ["test list", 2, False],
        {"test": "dict"},
    ]

    @app.route("/args_unpack")
    def return_args_unpack():
        return flask.jsonify(*a_list)

    @app.route("/array")
    def return_array():
        return flask.jsonify(a_list)

    for url in "/args_unpack", "/array":
        rv = client.get(url)
        assert rv.mimetype == "application/json"
        assert flask.json.loads(rv.data) == a_list


@pytest.mark.parametrize(
    "value", [datetime.datetime(1973, 3, 11, 6, 30, 45), datetime.date(1975, 1, 5)]
)
def test_jsonify_datetime(app, client, value):
    @app.route("/")
    def index():
        return flask.jsonify(value=value)

    r = client.get()
    assert r.json["value"] == http_date(value)


class FixedOffset(datetime.tzinfo):
    """Fixed offset in hours east from UTC.

    This is a slight adaptation of the ``FixedOffset`` example found in
    https://docs.python.org/2.7/library/datetime.html.
    """

    def __init__(self, hours, name):
        self.__offset = datetime.timedelta(hours=hours)
        self.__name = name

    def utcoffset(self, dt):
        return self.__offset

    def tzname(self, dt):
        return self.__name

    def dst(self, dt):
        return datetime.timedelta()


@pytest.mark.parametrize("tz", (("UTC", 0), ("PST", -8), ("KST", 9)))
def test_jsonify_aware_datetimes(tz):
    """Test if aware datetime.datetime objects are converted into GMT."""
    tzinfo = FixedOffset(hours=tz[1], name=tz[0])
    dt = datetime.datetime(2017, 1, 1, 12, 34, 56, tzinfo=tzinfo)
    gmt = FixedOffset(hours=0, name="GMT")
    expected = dt.astimezone(gmt).strftime('"%a, %d %b %Y %H:%M:%S %Z"')
    assert flask.json.dumps(dt) == expected


def test_jsonify_uuid_types(app, client):
    """Test jsonify with uuid.UUID types"""

    test_uuid = uuid.UUID(bytes=b"\xde\xad\xbe\xef" * 4)
    url = "/uuid_test"
    app.add_url_rule(url, url, lambda: flask.jsonify(x=test_uuid))

    rv = client.get(url)

    rv_x = flask.json.loads(rv.data)["x"]
    assert rv_x == str(test_uuid)
    rv_uuid = uuid.UUID(rv_x)
    assert rv_uuid == test_uuid


def test_json_decimal():
    rv = flask.json.dumps(decimal.Decimal("0.003"))
    assert rv == '"0.003"'


def test_json_attr(app, client):
    @app.route("/add", methods=["POST"])
    def add():
        json = flask.request.get_json()
        return str(json["a"] + json["b"])

    rv = client.post(
        "/add",
        data=flask.json.dumps({"a": 1, "b": 2}),
        content_type="application/json",
    )
    assert rv.data == b"3"


def test_tojson_filter(app, req_ctx):
    # The tojson filter is tested in Jinja, this confirms that it's
    # using Flask's dumps.
    rv = flask.render_template_string(
        "const data = {{ data|tojson }};",
        data={"name": "</script>", "time": datetime.datetime(2021, 2, 1, 7, 15)},
    )
    assert rv == (
        'const data = {"name": "\\u003c/script\\u003e",'
        ' "time": "Mon, 01 Feb 2021 07:15:00 GMT"};'
    )


def test_json_customization(app, client):
    class X:  # noqa: B903, for Python2 compatibility
        def __init__(self, val):
            self.val = val

    def default(o):
        if isinstance(o, X):
            return f"<{o.val}>"

        return DefaultJSONProvider.default(o)

    class CustomProvider(DefaultJSONProvider):
        def object_hook(self, obj):
            if len(obj) == 1 and "_foo" in obj:
                return X(obj["_foo"])

            return obj

        def loads(self, s, **kwargs):
            kwargs.setdefault("object_hook", self.object_hook)
            return super().loads(s, **kwargs)

    app.json = CustomProvider(app)
    app.json.default = default

    @app.route("/", methods=["POST"])
    def index():
        return flask.json.dumps(flask.request.get_json()["x"])

    rv = client.post(
        "/",
        data=flask.json.dumps({"x": {"_foo": 42}}),
        content_type="application/json",
    )
    assert rv.data == b'"<42>"'


def _has_encoding(name):
    try:
        import codecs

        codecs.lookup(name)
        return True
    except LookupError:
        return False


def test_json_key_sorting(app, client):
    app.debug = True
    assert app.json.sort_keys
    d = dict.fromkeys(range(20), "foo")

    @app.route("/")
    def index():
        return flask.jsonify(values=d)

    rv = client.get("/")
    lines = [x.strip() for x in rv.data.strip().decode("utf-8").splitlines()]
    sorted_by_str = [
        "{",
        '"values": {',
        '"0": "foo",',
        '"1": "foo",',
        '"10": "foo",',
        '"11": "foo",',
        '"12": "foo",',
        '"13": "foo",',
        '"14": "foo",',
        '"15": "foo",',
        '"16": "foo",',
        '"17": "foo",',
        '"18": "foo",',
        '"19": "foo",',
        '"2": "foo",',
        '"3": "foo",',
        '"4": "foo",',
        '"5": "foo",',
        '"6": "foo",',
        '"7": "foo",',
        '"8": "foo",',
        '"9": "foo"',
        "}",
        "}",
    ]
    sorted_by_int = [
        "{",
        '"values": {',
        '"0": "foo",',
        '"1": "foo",',
        '"2": "foo",',
        '"3": "foo",',
        '"4": "foo",',
        '"5": "foo",',
        '"6": "foo",',
        '"7": "foo",',
        '"8": "foo",',
        '"9": "foo",',
        '"10": "foo",',
        '"11": "foo",',
        '"12": "foo",',
        '"13": "foo",',
        '"14": "foo",',
        '"15": "foo",',
        '"16": "foo",',
        '"17": "foo",',
        '"18": "foo",',
        '"19": "foo"',
        "}",
        "}",
    ]

    try:
        assert lines == sorted_by_int
    except AssertionError:
        assert lines == sorted_by_str


def test_html_method():
    class ObjectWithHTML:
        def __html__(self):
            return "<p>test</p>"

    result = json.dumps(ObjectWithHTML())
    assert result == '"<p>test</p>"'


class CurrencyJSONProvider(DefaultJSONProvider):
    """A distinct representation to test blueprint providers. Serializes
    ``Decimal`` as an object with a currency, and parses JSON floats as
    ``Decimal``.
    """

    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return {"amount": str(o), "currency": "EUR"}

        return super().default(o)

    def loads(self, s, **kwargs):
        kwargs.setdefault("parse_float", decimal.Decimal)
        return super().loads(s, **kwargs)


def test_blueprint_json_provider(app, client):
    bp = flask.Blueprint("bp", __name__)
    bp.json_provider_class = CurrencyJSONProvider

    @bp.route("/dict")
    def bp_dict():
        return {"value": decimal.Decimal("1.5")}

    @bp.route("/jsonify")
    def bp_jsonify():
        return flask.jsonify(value=decimal.Decimal("1.5"))

    @app.route("/dict")
    def app_dict():
        return {"value": decimal.Decimal("1.5")}

    @app.route("/jsonify")
    def app_jsonify():
        return flask.jsonify(value=decimal.Decimal("1.5"))

    app.register_blueprint(bp, url_prefix="/bp")

    expected = {"value": {"amount": "1.5", "currency": "EUR"}}
    assert client.get("/bp/dict").get_json() == expected
    assert client.get("/bp/jsonify").get_json() == expected
    assert client.get("/dict").get_json() == {"value": "1.5"}
    assert client.get("/jsonify").get_json() == {"value": "1.5"}


def test_blueprint_json_provider_request(app, client):
    bp = flask.Blueprint("bp", __name__)
    bp.json_provider_class = CurrencyJSONProvider

    @bp.route("/echo", methods=["POST"])
    def bp_echo():
        data = flask.request.get_json()
        return {"type": type(data["value"]).__name__}

    @app.route("/echo", methods=["POST"])
    def app_echo():
        data = flask.request.get_json()
        return {"type": type(data["value"]).__name__}

    app.register_blueprint(bp, url_prefix="/bp")

    rv = client.post("/bp/echo", json={"value": 1.5})
    assert rv.get_json() == {"type": "Decimal"}
    rv = client.post("/echo", json={"value": 1.5})
    assert rv.get_json() == {"type": "float"}


def test_blueprint_json_provider_response_get_json(app, client):
    """get_json on a response uses the provider that created it, even
    after the request context is gone.
    """
    bp = flask.Blueprint("bp", __name__)
    bp.json_provider_class = CurrencyJSONProvider

    @bp.route("/")
    def bp_index():
        return {"value": 1.5}

    @app.route("/")
    def app_index():
        return {"value": 1.5}

    app.register_blueprint(bp, url_prefix="/bp")

    bp_rv = client.get("/bp/")
    app_rv = client.get("/")
    assert isinstance(bp_rv.get_json()["value"], decimal.Decimal)
    assert isinstance(app_rv.get_json()["value"], float)


def test_blueprint_json_provider_interleaved(app, client):
    """Interleaved requests and out-of-order parsing must not mix up
    providers.
    """
    bp = flask.Blueprint("bp", __name__)
    bp.json_provider_class = CurrencyJSONProvider

    @bp.route("/")
    def bp_index():
        return {"value": 1.5}

    @app.route("/")
    def app_index():
        return {"value": 1.5}

    app.register_blueprint(bp, url_prefix="/bp")

    responses = []

    for _ in range(3):
        responses.append((client.get("/bp/"), decimal.Decimal))
        responses.append((client.get("/"), float))

    for rv, type_ in reversed(responses):
        assert isinstance(rv.get_json()["value"], type_)


def test_blueprint_json_provider_nested(app, client):
    """The innermost blueprint with a provider is selected."""
    inner = flask.Blueprint("inner", __name__)
    inner.json_provider_class = CurrencyJSONProvider

    @inner.route("/")
    def inner_index():
        return {"value": 1.5}

    plain = flask.Blueprint("plain", __name__)

    @plain.route("/")
    def plain_index():
        return {"value": 1.5}

    outer = flask.Blueprint("outer", __name__)
    outer.json_provider_class = CurrencyJSONProvider
    outer.register_blueprint(inner, url_prefix="/inner")
    outer.register_blueprint(plain, url_prefix="/plain")
    app.register_blueprint(outer, url_prefix="/outer")

    rv = client.get("/outer/inner/")
    assert isinstance(rv.get_json()["value"], decimal.Decimal)
    # A nested blueprint without a provider uses its parent's.
    rv = client.get("/outer/plain/")
    assert isinstance(rv.get_json()["value"], decimal.Decimal)


def test_blueprint_json_provider_per_registration(app, client):
    """The same blueprint object registered under different names can
    use a different provider for each registration.
    """
    bp = flask.Blueprint("bp", __name__)

    @bp.route("/")
    def index():
        return {"value": 1.5}

    app.register_blueprint(bp, name="old", url_prefix="/old")
    app.register_blueprint(
        bp, name="new", url_prefix="/new", json_provider=CurrencyJSONProvider
    )
    app.register_blueprint(
        bp, name="newer", url_prefix="/newer", json_provider=CurrencyJSONProvider(app)
    )

    assert isinstance(client.get("/old/").get_json()["value"], float)
    assert isinstance(client.get("/new/").get_json()["value"], decimal.Decimal)
    assert isinstance(client.get("/newer/").get_json()["value"], decimal.Decimal)


def test_blueprint_json_provider_error(app, client):
    """Error responses for a matched request use its blueprint's
    provider. Unmatched requests use the app's provider.
    """
    bp = flask.Blueprint("bp", __name__)
    bp.json_provider_class = CurrencyJSONProvider

    @bp.route("/missing")
    def bp_missing():
        flask.abort(404)

    @bp.errorhandler(404)
    def bp_not_found(e):
        return {"value": 1.5}, 404

    @app.errorhandler(404)
    def app_not_found(e):
        return {"value": 1.5}, 404

    app.register_blueprint(bp, url_prefix="/bp")

    rv = client.get("/bp/missing")
    assert isinstance(rv.get_json()["value"], decimal.Decimal)
    # No route matched, the app's provider is used.
    rv = client.get("/bp/other")
    assert isinstance(rv.get_json()["value"], float)
    rv = client.get("/other")
    assert isinstance(rv.get_json()["value"], float)


def test_blueprint_json_provider_app_context(app):
    """Without a matched request, the app's provider is used."""
    bp = flask.Blueprint("bp", __name__)
    bp.json_provider_class = CurrencyJSONProvider
    app.register_blueprint(bp)

    with app.app_context():
        rv = flask.jsonify(value=1.5)
        assert isinstance(rv.get_json()["value"], float)
        assert isinstance(app.json.loads("1.5"), float)


def test_blueprint_json_provider_explicit_app_json(app, client):
    """Explicit uses of app.json keep using the app's provider, even
    during a blueprint request.
    """
    bp = flask.Blueprint("bp", __name__)
    bp.json_provider_class = CurrencyJSONProvider

    @bp.route("/response")
    def bp_response():
        return flask.current_app.json.response({"value": 1.5})

    @bp.route("/dumps")
    def bp_dumps():
        return flask.json.dumps({"value": decimal.Decimal("1.5")})

    app.register_blueprint(bp, url_prefix="/bp")

    rv = client.get("/bp/response")
    assert isinstance(rv.get_json()["value"], float)
    rv = client.get("/bp/dumps")
    assert rv.get_data(as_text=True) == '{"value": "1.5"}'
