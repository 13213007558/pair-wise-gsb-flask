import logging
import os

import pytest
import werkzeug.serving
from jinja2 import FileSystemBytecodeCache
from jinja2 import TemplateNotFound
from markupsafe import Markup

import flask


def test_context_processing(app, client):
    @app.context_processor
    def context_processor():
        return {"injected_value": 42}

    @app.route("/")
    def index():
        return flask.render_template("context_template.html", value=23)

    rv = client.get("/")
    assert rv.data == b"<p>23|42"


def test_original_win(app, client):
    @app.route("/")
    def index():
        return flask.render_template_string("{{ config }}", config=42)

    rv = client.get("/")
    assert rv.data == b"42"


def test_simple_stream(app, client):
    @app.route("/")
    def index():
        return flask.stream_template_string("{{ config }}", config=42)

    rv = client.get("/")
    assert rv.data == b"42"


def test_request_less_rendering(app, app_ctx):
    app.config["WORLD_NAME"] = "Special World"

    @app.context_processor
    def context_processor():
        return dict(foo=42)

    rv = flask.render_template_string("Hello {{ config.WORLD_NAME }} {{ foo }}")
    assert rv == "Hello Special World 42"


def test_standard_context(app, client):
    @app.route("/")
    def index():
        flask.g.foo = 23
        flask.session["test"] = "aha"
        return flask.render_template_string(
            """
            {{ request.args.foo }}
            {{ g.foo }}
            {{ config.DEBUG }}
            {{ session.test }}
        """
        )

    rv = client.get("/?foo=42")
    assert rv.data.split() == [b"42", b"23", b"False", b"aha"]


def test_escaping(app, client):
    text = "<p>Hello World!"

    @app.route("/")
    def index():
        return flask.render_template(
            "escaping_template.html", text=text, html=Markup(text)
        )

    lines = client.get("/").data.splitlines()
    assert lines == [
        b"&lt;p&gt;Hello World!",
        b"<p>Hello World!",
        b"<p>Hello World!",
        b"<p>Hello World!",
        b"&lt;p&gt;Hello World!",
        b"<p>Hello World!",
    ]


def test_no_escaping(app, client):
    text = "<p>Hello World!"

    @app.route("/")
    def index():
        return flask.render_template(
            "non_escaping_template.txt", text=text, html=Markup(text)
        )

    lines = client.get("/").data.splitlines()
    assert lines == [
        b"<p>Hello World!",
        b"<p>Hello World!",
        b"<p>Hello World!",
        b"<p>Hello World!",
        b"&lt;p&gt;Hello World!",
        b"<p>Hello World!",
        b"<p>Hello World!",
        b"<p>Hello World!",
    ]


def test_escaping_without_template_filename(app, client, req_ctx):
    assert flask.render_template_string("{{ foo }}", foo="<test>") == "&lt;test&gt;"
    assert flask.render_template("mail.txt", foo="<test>") == "<test> Mail"


def test_macros(app, req_ctx):
    macro = flask.get_template_attribute("_macro.html", "hello")
    assert macro("World") == "Hello World!"


def test_template_filter(app):
    @app.template_filter()
    def my_reverse(s):
        return s[::-1]

    assert "my_reverse" in app.jinja_env.filters.keys()
    assert app.jinja_env.filters["my_reverse"] == my_reverse
    assert app.jinja_env.filters["my_reverse"]("abcd") == "dcba"

    @app.template_filter
    def my_reverse_2(s):
        return s[::-1]

    assert "my_reverse_2" in app.jinja_env.filters.keys()
    assert app.jinja_env.filters["my_reverse_2"] == my_reverse_2
    assert app.jinja_env.filters["my_reverse_2"]("abcd") == "dcba"

    @app.template_filter("my_reverse_custom_name_3")
    def my_reverse_3(s):
        return s[::-1]

    assert "my_reverse_custom_name_3" in app.jinja_env.filters.keys()
    assert app.jinja_env.filters["my_reverse_custom_name_3"] == my_reverse_3
    assert app.jinja_env.filters["my_reverse_custom_name_3"]("abcd") == "dcba"

    @app.template_filter(name="my_reverse_custom_name_4")
    def my_reverse_4(s):
        return s[::-1]

    assert "my_reverse_custom_name_4" in app.jinja_env.filters.keys()
    assert app.jinja_env.filters["my_reverse_custom_name_4"] == my_reverse_4
    assert app.jinja_env.filters["my_reverse_custom_name_4"]("abcd") == "dcba"


def test_add_template_filter(app):
    def my_reverse(s):
        return s[::-1]

    app.add_template_filter(my_reverse)
    assert "my_reverse" in app.jinja_env.filters.keys()
    assert app.jinja_env.filters["my_reverse"] == my_reverse
    assert app.jinja_env.filters["my_reverse"]("abcd") == "dcba"


def test_template_filter_with_name(app):
    @app.template_filter("strrev")
    def my_reverse(s):
        return s[::-1]

    assert "strrev" in app.jinja_env.filters.keys()
    assert app.jinja_env.filters["strrev"] == my_reverse
    assert app.jinja_env.filters["strrev"]("abcd") == "dcba"


def test_add_template_filter_with_name(app):
    def my_reverse(s):
        return s[::-1]

    app.add_template_filter(my_reverse, "strrev")
    assert "strrev" in app.jinja_env.filters.keys()
    assert app.jinja_env.filters["strrev"] == my_reverse
    assert app.jinja_env.filters["strrev"]("abcd") == "dcba"


def test_template_filter_with_template(app, client):
    @app.template_filter()
    def super_reverse(s):
        return s[::-1]

    @app.route("/")
    def index():
        return flask.render_template("template_filter.html", value="abcd")

    rv = client.get("/")
    assert rv.data == b"dcba"


def test_add_template_filter_with_template(app, client):
    def super_reverse(s):
        return s[::-1]

    app.add_template_filter(super_reverse)

    @app.route("/")
    def index():
        return flask.render_template("template_filter.html", value="abcd")

    rv = client.get("/")
    assert rv.data == b"dcba"


def test_template_filter_with_name_and_template(app, client):
    @app.template_filter("super_reverse")
    def my_reverse(s):
        return s[::-1]

    @app.route("/")
    def index():
        return flask.render_template("template_filter.html", value="abcd")

    rv = client.get("/")
    assert rv.data == b"dcba"


def test_add_template_filter_with_name_and_template(app, client):
    def my_reverse(s):
        return s[::-1]

    app.add_template_filter(my_reverse, "super_reverse")

    @app.route("/")
    def index():
        return flask.render_template("template_filter.html", value="abcd")

    rv = client.get("/")
    assert rv.data == b"dcba"


def test_template_test(app):
    @app.template_test()
    def boolean(value):
        return isinstance(value, bool)

    assert "boolean" in app.jinja_env.tests.keys()
    assert app.jinja_env.tests["boolean"] == boolean
    assert app.jinja_env.tests["boolean"](False)

    @app.template_test
    def boolean_2(value):
        return isinstance(value, bool)

    assert "boolean_2" in app.jinja_env.tests.keys()
    assert app.jinja_env.tests["boolean_2"] == boolean_2
    assert app.jinja_env.tests["boolean_2"](False)

    @app.template_test("my_boolean_custom_name")
    def boolean_3(value):
        return isinstance(value, bool)

    assert "my_boolean_custom_name" in app.jinja_env.tests.keys()
    assert app.jinja_env.tests["my_boolean_custom_name"] == boolean_3
    assert app.jinja_env.tests["my_boolean_custom_name"](False)

    @app.template_test(name="my_boolean_custom_name_2")
    def boolean_4(value):
        return isinstance(value, bool)

    assert "my_boolean_custom_name_2" in app.jinja_env.tests.keys()
    assert app.jinja_env.tests["my_boolean_custom_name_2"] == boolean_4
    assert app.jinja_env.tests["my_boolean_custom_name_2"](False)


def test_add_template_test(app):
    def boolean(value):
        return isinstance(value, bool)

    app.add_template_test(boolean)
    assert "boolean" in app.jinja_env.tests.keys()
    assert app.jinja_env.tests["boolean"] == boolean
    assert app.jinja_env.tests["boolean"](False)


def test_template_test_with_name(app):
    @app.template_test("boolean")
    def is_boolean(value):
        return isinstance(value, bool)

    assert "boolean" in app.jinja_env.tests.keys()
    assert app.jinja_env.tests["boolean"] == is_boolean
    assert app.jinja_env.tests["boolean"](False)


def test_add_template_test_with_name(app):
    def is_boolean(value):
        return isinstance(value, bool)

    app.add_template_test(is_boolean, "boolean")
    assert "boolean" in app.jinja_env.tests.keys()
    assert app.jinja_env.tests["boolean"] == is_boolean
    assert app.jinja_env.tests["boolean"](False)


def test_template_test_with_template(app, client):
    @app.template_test()
    def boolean(value):
        return isinstance(value, bool)

    @app.route("/")
    def index():
        return flask.render_template("template_test.html", value=False)

    rv = client.get("/")
    assert b"Success!" in rv.data


def test_add_template_test_with_template(app, client):
    def boolean(value):
        return isinstance(value, bool)

    app.add_template_test(boolean)

    @app.route("/")
    def index():
        return flask.render_template("template_test.html", value=False)

    rv = client.get("/")
    assert b"Success!" in rv.data


def test_template_test_with_name_and_template(app, client):
    @app.template_test("boolean")
    def is_boolean(value):
        return isinstance(value, bool)

    @app.route("/")
    def index():
        return flask.render_template("template_test.html", value=False)

    rv = client.get("/")
    assert b"Success!" in rv.data


def test_add_template_test_with_name_and_template(app, client):
    def is_boolean(value):
        return isinstance(value, bool)

    app.add_template_test(is_boolean, "boolean")

    @app.route("/")
    def index():
        return flask.render_template("template_test.html", value=False)

    rv = client.get("/")
    assert b"Success!" in rv.data


def test_add_template_global(app, app_ctx):
    @app.template_global()
    def get_stuff():
        return 42

    assert "get_stuff" in app.jinja_env.globals.keys()
    assert app.jinja_env.globals["get_stuff"] == get_stuff
    assert app.jinja_env.globals["get_stuff"](), 42

    rv = flask.render_template_string("{{ get_stuff() }}")
    assert rv == "42"

    @app.template_global
    def get_stuff_1():
        return "get_stuff_1"

    assert "get_stuff_1" in app.jinja_env.globals.keys()
    assert app.jinja_env.globals["get_stuff_1"] == get_stuff_1
    assert app.jinja_env.globals["get_stuff_1"](), "get_stuff_1"

    rv = flask.render_template_string("{{ get_stuff_1() }}")
    assert rv == "get_stuff_1"

    @app.template_global("my_get_stuff_custom_name_2")
    def get_stuff_2():
        return "get_stuff_2"

    assert "my_get_stuff_custom_name_2" in app.jinja_env.globals.keys()
    assert app.jinja_env.globals["my_get_stuff_custom_name_2"] == get_stuff_2
    assert app.jinja_env.globals["my_get_stuff_custom_name_2"](), "get_stuff_2"

    rv = flask.render_template_string("{{ my_get_stuff_custom_name_2() }}")
    assert rv == "get_stuff_2"

    @app.template_global(name="my_get_stuff_custom_name_3")
    def get_stuff_3():
        return "get_stuff_3"

    assert "my_get_stuff_custom_name_3" in app.jinja_env.globals.keys()
    assert app.jinja_env.globals["my_get_stuff_custom_name_3"] == get_stuff_3
    assert app.jinja_env.globals["my_get_stuff_custom_name_3"](), "get_stuff_3"

    rv = flask.render_template_string("{{ my_get_stuff_custom_name_3() }}")
    assert rv == "get_stuff_3"


def test_custom_template_loader(client):
    class MyFlask(flask.Flask):
        def create_global_jinja_loader(self):
            from jinja2 import DictLoader

            return DictLoader({"index.html": "Hello Custom World!"})

    app = MyFlask(__name__)

    @app.route("/")
    def index():
        return flask.render_template("index.html")

    c = app.test_client()
    rv = c.get("/")
    assert rv.data == b"Hello Custom World!"


def test_iterable_loader(app, client):
    @app.context_processor
    def context_processor():
        return {"whiskey": "Jameson"}

    @app.route("/")
    def index():
        return flask.render_template(
            [
                "no_template.xml",  # should skip this one
                "simple_template.html",  # should render this
                "context_template.html",
            ],
            value=23,
        )

    rv = client.get("/")
    assert rv.data == b"<h1>Jameson</h1>"


def test_templates_auto_reload(app):
    # debug is False, config option is None
    assert app.debug is False
    assert app.config["TEMPLATES_AUTO_RELOAD"] is None
    assert app.jinja_env.auto_reload is False
    # debug is False, config option is False
    app = flask.Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = False
    assert app.debug is False
    assert app.jinja_env.auto_reload is False
    # debug is False, config option is True
    app = flask.Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    assert app.debug is False
    assert app.jinja_env.auto_reload is True
    # debug is True, config option is None
    app = flask.Flask(__name__)
    app.config["DEBUG"] = True
    assert app.config["TEMPLATES_AUTO_RELOAD"] is None
    assert app.jinja_env.auto_reload is True
    # debug is True, config option is False
    app = flask.Flask(__name__)
    app.config["DEBUG"] = True
    app.config["TEMPLATES_AUTO_RELOAD"] = False
    assert app.jinja_env.auto_reload is False
    # debug is True, config option is True
    app = flask.Flask(__name__)
    app.config["DEBUG"] = True
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    assert app.jinja_env.auto_reload is True


def test_templates_auto_reload_debug_run(app, monkeypatch):
    def run_simple_mock(*args, **kwargs):
        pass

    monkeypatch.setattr(werkzeug.serving, "run_simple", run_simple_mock)

    app.run()
    assert not app.jinja_env.auto_reload

    app.run(debug=True)
    assert app.jinja_env.auto_reload


def test_template_loader_debugging(test_apps, monkeypatch):
    from blueprintapp import app

    called = []

    class _TestHandler(logging.Handler):
        def handle(self, record):
            called.append(True)
            text = str(record.msg)
            assert "1: trying loader of application 'blueprintapp'" in text
            assert (
                "2: trying loader of blueprint 'admin' (blueprintapp.apps.admin)"
            ) in text
            assert (
                "trying loader of blueprint 'frontend' (blueprintapp.apps.frontend)"
            ) in text
            assert "Error: the template could not be found" in text
            assert (
                "looked up from an endpoint that belongs to the blueprint 'frontend'"
            ) in text
            assert "See https://flask.palletsprojects.com/blueprints/#templates" in text

    with app.test_client() as c:
        monkeypatch.setitem(app.config, "EXPLAIN_TEMPLATE_LOADING", True)
        monkeypatch.setattr(
            logging.getLogger("blueprintapp"), "handlers", [_TestHandler()]
        )

        with pytest.raises(TemplateNotFound) as excinfo:
            c.get("/missing")

        assert "missing_template.html" in str(excinfo.value)

    assert len(called) == 1


def test_custom_jinja_env():
    class CustomEnvironment(flask.templating.Environment):
        pass

    class CustomFlask(flask.Flask):
        jinja_environment = CustomEnvironment

    app = CustomFlask(__name__)
    assert isinstance(app.jinja_env, CustomEnvironment)


def _make_plugin(tmp_path, name, layout, page, partial):
    """Create a blueprint with its own template folder in tmp_path."""
    folder = tmp_path / name
    folder.mkdir()
    (folder / "layout.html").write_text(layout)
    (folder / "page.html").write_text(page)
    (folder / "partial.html").write_text(partial)
    return flask.Blueprint(name, __name__, template_folder=str(folder))


@pytest.fixture
def plugin_app(tmp_path):
    """An app with two plugins that have same-named but different
    templates. Each page adds one level of inheritance and one include.
    """
    app = flask.Flask(__name__, template_folder=str(tmp_path / "app_templates"))
    (tmp_path / "app_templates").mkdir()
    report = _make_plugin(
        tmp_path,
        "report",
        "[report-layout]{% block body %}{% endblock %}",
        '{% extends "layout.html" %}{% block body %}'
        'report-page|{% include "partial.html" %}'
        '|{% include "analytics::partial.html" %}{% endblock %}',
        "report-partial",
    )
    analytics = _make_plugin(
        tmp_path,
        "analytics",
        "[analytics-layout]{% block body %}{% endblock %}",
        '{% extends "layout.html" %}{% block body %}'
        'analytics-page|{% include "partial.html" %}{% endblock %}',
        "analytics-partial",
    )
    app.register_blueprint(report)
    app.register_blueprint(analytics)
    return app


def test_namespaced_templates_interleaved(plugin_app):
    """Each plugin renders its own layout and partial. Warm the cache,
    then interleave renders to check the cache identity does not leak
    across namespaces.
    """
    expected = {
        "report::page.html": (
            "[report-layout]report-page|report-partial|analytics-partial"
        ),
        "analytics::page.html": ("[analytics-layout]analytics-page|analytics-partial"),
    }

    with plugin_app.app_context():
        for name, expected_out in expected.items():
            assert flask.render_template(name) == expected_out

        for _ in range(3):
            for name, expected_out in expected.items():
                assert flask.render_template(name) == expected_out


def test_namespaced_template_override(plugin_app, tmp_path):
    """templates/overrides/<name>/... in the app overrides the plugin's
    template. The override's relative dependencies still resolve in the
    plugin's namespace.
    """
    override_dir = tmp_path / "app_templates" / "overrides" / "report"
    override_dir.mkdir(parents=True)
    (override_dir / "page.html").write_text(
        '{% extends "layout.html" %}{% block body %}'
        'override|{% include "partial.html" %}{% endblock %}'
    )

    with plugin_app.app_context():
        assert (
            flask.render_template("report::page.html")
            == "[report-layout]override|report-partial"
        )
        # The other plugin is not affected by the override.
        assert (
            flask.render_template("analytics::page.html")
            == "[analytics-layout]analytics-page|analytics-partial"
        )


def test_namespaced_nested_and_multiple_registration(plugin_app, tmp_path):
    """Nested registration names and multiple registrations of one
    blueprint are distinct namespaces.
    """
    extra = _make_plugin(
        tmp_path,
        "extra",
        "[extra-layout]{% block body %}{% endblock %}",
        '{% extends "layout.html" %}{% block body %}extra-page{% endblock %}',
        "extra-partial",
    )
    admin = flask.Blueprint("admin", __name__)
    admin.register_blueprint(extra)
    plugin_app.register_blueprint(admin)
    plugin_app.register_blueprint(extra, name="extra2")

    with plugin_app.app_context():
        assert (
            flask.render_template("admin.extra::page.html")
            == "[extra-layout]extra-page"
        )
        assert flask.render_template("extra2::page.html") == "[extra-layout]extra-page"
        # The nested registration is not visible under the bare inner name.
        with pytest.raises(TemplateNotFound):
            flask.render_template("extra::page.html")


def test_namespaced_template_unknown_namespace(plugin_app):
    with plugin_app.app_context(), pytest.raises(TemplateNotFound):
        flask.render_template("unknown::page.html")


def test_bare_template_lookup_unchanged(plugin_app, tmp_path):
    """Bare names keep the previous lookup order: app first, then
    blueprints in registration order.
    """
    with plugin_app.app_context():
        # The app has no page.html, so the first registered plugin wins.
        assert (
            flask.render_template("page.html")
            == "[report-layout]report-page|report-partial|analytics-partial"
        )

    # An app template with the same bare name still takes precedence.
    (tmp_path / "app_templates" / "page.html").write_text("app-page")
    plugin_app.jinja_env.cache.clear()

    with plugin_app.app_context():
        assert flask.render_template("page.html") == "app-page"


def test_namespaced_template_caches(plugin_app, tmp_path):
    """Template cache, bytecode cache, and auto-reload use identities
    that do not conflict across namespaces.
    """
    (tmp_path / "bytecode").mkdir()
    plugin_app.jinja_env.bytecode_cache = FileSystemBytecodeCache(
        str(tmp_path / "bytecode")
    )
    plugin_app.jinja_env.auto_reload = True

    with plugin_app.app_context():
        assert flask.render_template("report::page.html").startswith("[report-layout]")
        assert flask.render_template("analytics::page.html").startswith(
            "[analytics-layout]"
        )

    assert os.listdir(tmp_path / "bytecode")

    # Modify only one plugin's page. Auto-reload must pick up the change
    # for that namespace only.
    page = tmp_path / "report" / "page.html"
    page.write_text(
        '{% extends "layout.html" %}{% block body %}report-v2{% endblock %}'
    )
    mtime = os.stat(page).st_mtime + 10
    os.utime(page, (mtime, mtime))

    with plugin_app.app_context():
        assert flask.render_template("report::page.html") == "[report-layout]report-v2"
        assert (
            flask.render_template("analytics::page.html")
            == "[analytics-layout]analytics-page|analytics-partial"
        )


def test_namespaced_explain_template_loading(plugin_app, monkeypatch):
    """Toggling EXPLAIN_TEMPLATE_LOADING does not change which template
    is selected, and a failed lookup shows the searched namespaces.
    """
    records = []

    class _Handler(logging.Handler):
        def handle(self, record):
            records.append(str(record.msg))

    logger = logging.getLogger(plugin_app.name)
    monkeypatch.setattr(logger, "handlers", [_Handler()])
    monkeypatch.setattr(logger, "level", logging.INFO)

    for explain in (False, True):
        monkeypatch.setitem(plugin_app.config, "EXPLAIN_TEMPLATE_LOADING", explain)

        with plugin_app.app_context():
            assert flask.render_template("report::page.html") == (
                "[report-layout]report-page|report-partial|analytics-partial"
            )

    with (
        plugin_app.app_context(),
        pytest.raises(TemplateNotFound, match="report::missing.html"),
    ):
        flask.render_template("report::missing.html")

    text = "\n".join(records)
    assert "Locating template 'report::missing.html':" in text
    assert "application override for namespace 'report'" in text
    assert "trying loader of blueprint 'report'" in text


def test_list_templates_namespaced(plugin_app):
    templates = plugin_app.jinja_env.loader.list_templates()
    assert "report::page.html" in templates
    assert "report::layout.html" in templates
    assert "analytics::page.html" in templates
