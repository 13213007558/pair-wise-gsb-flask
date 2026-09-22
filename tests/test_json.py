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
    """Serialize ``Decimal`` as an object with an amount and currency,
    and parse such objects back to ``Decimal``.
    """

    currency = "EUR"

    @classmethod
    def default(cls, o):
        if isinstance(o, decimal.Decimal):
            return {"amount": str(o), "currency": cls.currency}

        return DefaultJSONProvider.default(o)

    def loads(self, s, **kwargs):
        def object_hook(d):
            if set(d) == {"amount", "currency"}:
                return decimal.Decimal(d["amount"])

            return d

        kwargs.setdefault("object_hook", object_hook)
        return super().loads(s, **kwargs)


class OtherCurrencyJSONProvider(CurrencyJSONProvider):
    currency = "USD"


def make_currency_bp(**kwargs):
    bp = flask.Blueprint("currency", __name__)
    bp.json_provider_class = CurrencyJSONProvider

    @bp.get("/price")
    def price():
        return {"price": decimal.Decimal("1.5")}

    @bp.get("/jsonify")
    def price_jsonify():
        return flask.jsonify(price=decimal.Decimal("1.5"))

    @bp.post("/echo")
    def echo():
        data = flask.request.get_json()
        return {"type": type(data["price"]).__name__}

    return bp


def test_blueprint_json_provider_dict(app, client):
    app.register_blueprint(make_currency_bp(), url_prefix="/bp")

    @app.get("/price")
    def app_price():
        return {"price": decimal.Decimal("1.5")}

    rv = client.get("/bp/price")
    assert rv.get_json() == {"price": decimal.Decimal("1.5")}
    assert b'"currency":"EUR"' in rv.data

    rv = client.get("/price")
    assert rv.get_json() == {"price": "1.5"}
    assert b"currency" not in rv.data


def test_blueprint_json_provider_jsonify(app, client):
    app.register_blueprint(make_currency_bp(), url_prefix="/bp")

    @app.get("/jsonify")
    def app_jsonify():
        return flask.jsonify(price=decimal.Decimal("1.5"))

    rv = client.get("/bp/jsonify")
    assert rv.get_json() == {"price": decimal.Decimal("1.5")}

    rv = client.get("/jsonify")
    assert rv.get_json() == {"price": "1.5"}


def test_blueprint_json_provider_request_get_json(app, client):
    app.register_blueprint(make_currency_bp(), url_prefix="/bp")

    @app.post("/echo")
    def app_echo():
        data = flask.request.get_json()
        return {"type": type(data["price"]).__name__}

    data = '{"price": {"amount": "2.5", "currency": "EUR"}}'
    rv = client.post("/bp/echo", data=data, content_type="application/json")
    assert rv.get_json() == {"type": "Decimal"}

    rv = client.post("/echo", data=data, content_type="application/json")
    assert rv.get_json() == {"type": "dict"}


def test_blueprint_json_provider_nested(app, client):
    inner = flask.Blueprint("inner", __name__)

    @inner.get("/price")
    def inner_price():
        return {"price": decimal.Decimal("1.5")}

    own = flask.Blueprint("own", __name__)
    own.json_provider_class = OtherCurrencyJSONProvider

    @own.get("/price")
    def own_price():
        return {"price": decimal.Decimal("1.5")}

    outer = flask.Blueprint("outer", __name__)
    outer.json_provider_class = CurrencyJSONProvider
    outer.register_blueprint(inner, url_prefix="/inner")
    outer.register_blueprint(own, url_prefix="/own")
    app.register_blueprint(outer, url_prefix="/outer")

    # The inner blueprint inherits the outer blueprint's provider.
    rv = client.get("/outer/inner/price")
    assert b'"currency":"EUR"' in rv.data

    # A blueprint's own provider takes precedence over the outer one.
    rv = client.get("/outer/own/price")
    assert b'"currency":"USD"' in rv.data


def test_blueprint_json_provider_per_registration(app, client):
    bp = flask.Blueprint("api", __name__)

    @bp.get("/price")
    def price():
        return {"price": decimal.Decimal("1.5")}

    app.register_blueprint(
        bp,
        name="eur",
        url_prefix="/eur",
        json_provider_class=CurrencyJSONProvider,
    )
    app.register_blueprint(
        bp,
        name="usd",
        url_prefix="/usd",
        json_provider_class=OtherCurrencyJSONProvider,
    )

    rv = client.get("/eur/price")
    assert b'"currency":"EUR"' in rv.data

    rv = client.get("/usd/price")
    assert b'"currency":"USD"' in rv.data


def test_blueprint_json_provider_error(app, client):
    app.register_blueprint(make_currency_bp(), url_prefix="/bp")

    @app.errorhandler(404)
    def not_found(e):
        return {"error": decimal.Decimal("0.1")}, 404

    @app.get("/app-missing")
    def app_missing():
        flask.abort(404)

    bp_err = flask.Blueprint("bp_err", __name__)
    bp_err.json_provider_class = CurrencyJSONProvider

    @bp_err.get("/missing")
    def bp_err_missing():
        flask.abort(404)

    app.register_blueprint(bp_err, url_prefix="/bp-err")

    # A request that matched a blueprint route uses the blueprint's
    # provider for error responses too.
    rv = client.get("/bp-err/missing")
    assert rv.status_code == 404
    assert rv.get_json() == {"error": decimal.Decimal("0.1")}

    # A request that matched an app route uses the app's provider.
    rv = client.get("/app-missing")
    assert rv.get_json() == {"error": "0.1"}

    # A request that did not match a route uses the app's provider.
    rv = client.get("/no-such-route")
    assert rv.get_json() == {"error": "0.1"}


def test_blueprint_json_provider_response_get_json_after_request(app, client):
    app.register_blueprint(make_currency_bp(), url_prefix="/bp")

    @app.get("/price")
    def app_price():
        return {"price": decimal.Decimal("1.5")}

    # Interleave requests to both providers, then parse the responses
    # in reverse order, after each request has ended.
    rv_app = client.get("/price")
    rv_bp = client.get("/bp/price")
    rv_app2 = client.get("/price")

    assert rv_app2.get_json() == {"price": "1.5"}
    assert rv_bp.get_json() == {"price": decimal.Decimal("1.5")}
    assert rv_app.get_json() == {"price": "1.5"}


def test_blueprint_json_provider_response_object_after_request(app):
    bp = flask.Blueprint("currency", __name__)
    bp.json_provider_class = CurrencyJSONProvider

    @bp.get("/price")
    def price():
        return {"price": decimal.Decimal("1.5")}

    app.register_blueprint(bp, url_prefix="/bp")

    with app.test_request_context("/bp/price"):
        rv_bp = flask.jsonify(price=decimal.Decimal("1.5"))

    with app.test_request_context("/other"):
        rv_app = flask.jsonify(price=decimal.Decimal("1.5"))

    # The request contexts have ended, but each response still uses the
    # provider that generated it.
    assert rv_bp.get_json() == {"price": decimal.Decimal("1.5")}
    assert rv_app.get_json() == {"price": "1.5"}


def test_blueprint_json_provider_app_context_default(app):
    app.register_blueprint(make_currency_bp(), url_prefix="/bp")

    # Outside a request, the app's provider is used.
    with app.app_context():
        assert flask.json.dumps(decimal.Decimal("1.5")) == '"1.5"'
        assert app.json.dumps(decimal.Decimal("1.5")) == '"1.5"'
