import logging

import pytest
import werkzeug.serving
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


class TestAutoEscapePolicy:
    """The configurable JINJA_AUTOESCAPE_POLICY replaces extension-only
    decisions while keeping the historical default."""

    sample = '<b id="x">&amp; "q" \'a\''
    escaped = "&lt;b id=&#34;x&#34;&gt;&amp;amp; &#34;q&#34; &#39;a&#39;"

    def test_default_policy_is_historical_selective(self, app, req_ctx):
        assert app.config["JINJA_AUTOESCAPE_POLICY"] == "selective"
        assert isinstance(
            app.jinja_autoescape_policy,
            flask.templating.SelectiveAutoEscapePolicy,
        )
        assert app.select_jinja_autoescape("page.html") is True
        assert app.select_jinja_autoescape("page.SVG") is True
        assert app.select_jinja_autoescape("mail.txt") is False
        assert app.select_jinja_autoescape("page.jinja") is False
        assert app.select_jinja_autoescape(None) is True

    def test_render_paths_share_the_environment_policy(self, app, req_ctx):
        app.config["JINJA_AUTOESCAPE_POLICY"] = "html"

        rendered = flask.render_template_string("{{ v }}", v=self.sample)
        streamed = "".join(flask.stream_template_string("{{ v }}", v=self.sample))
        assert rendered == self.escaped
        assert streamed == self.escaped

        assert (
            flask.render_template("simple_template.html", whiskey=self.sample)
            == f"<h1>{self.escaped}</h1>"
        )

    def test_strict_policy_escapes_unknown_and_user_chosen_names(self, app, req_ctx):
        app.config["JINJA_AUTOESCAPE_POLICY"] = "html"
        policy = app.jinja_autoescape_policy

        for name in ("user.jinja", "user", "user.xyz", "PAGE.HTML", None):
            assert policy(name) is True, name

        assert policy("mail.txt") is False
        assert policy("plain.text") is False

    def test_strict_policy_custom_text_extensions(self, req_ctx):
        app = flask.Flask(__name__)
        app.config["JINJA_AUTOESCAPE_POLICY"] = flask.templating.HTMLAutoEscapePolicy(
            non_html_extensions={".email", ".md"}
        )
        policy = app.jinja_autoescape_policy
        assert policy("message.email") is False
        assert policy("message.md") is False
        assert policy("message.txt") is True
        assert policy("message.jinja") is True

    def test_text_policy_never_escapes_but_respects_safe(self, app, req_ctx):
        app.config["JINJA_AUTOESCAPE_POLICY"] = "text"
        assert app.select_jinja_autoescape("anything.html") is False

        assert flask.render_template_string("{{ v }}", v=self.sample) == (self.sample)
        source = "{{ a }}|{{ b }}"
        rendered = flask.render_template_string(
            source, a=self.sample, b=Markup(self.sample)
        )
        assert rendered == f"{self.sample}|{self.sample}"

    def test_policy_instance_and_subclass_in_config(self, req_ctx):
        class ShoutPolicy(flask.templating.AutoEscapePolicy):
            def should_escape(self, template_name):
                return template_name != "raw.txt"

        app = flask.Flask(__name__)
        app.config["JINJA_AUTOESCAPE_POLICY"] = ShoutPolicy
        assert isinstance(app.jinja_autoescape_policy, ShoutPolicy)
        assert app.jinja_autoescape_policy("raw.txt") is False
        assert app.jinja_autoescape_policy("other.html") is True

        app2 = flask.Flask(__name__)
        instance = ShoutPolicy()
        app2.config["JINJA_AUTOESCAPE_POLICY"] = instance
        assert app2.jinja_autoescape_policy is instance

    def test_plain_callable_policy(self, req_ctx):
        app = flask.Flask(__name__)
        app.config["JINJA_AUTOESCAPE_POLICY"] = lambda name: name is None
        assert app.jinja_autoescape_policy(None) is True
        assert app.jinja_autoescape_policy("x.html") is False

    def test_unknown_policy_name_raises(self, req_ctx):
        app = flask.Flask(__name__)
        app.config["JINJA_AUTOESCAPE_POLICY"] = "bogus"
        with pytest.raises(RuntimeError, match="Unknown JINJA_AUTOESCAPE_POLICY"):
            app.jinja_env  # noqa: B018

    def test_invalid_policy_value_raises(self, req_ctx):
        app = flask.Flask(__name__)
        app.config["JINJA_AUTOESCAPE_POLICY"] = 123
        with pytest.raises(RuntimeError, match="must be a policy name"):
            app.jinja_env  # noqa: B018

    def test_jinja_options_autoescape_still_wins(self, req_ctx):
        app = flask.Flask(__name__)
        app.config["JINJA_AUTOESCAPE_POLICY"] = "html"
        # jinja_options is a class-level dict, restore it to avoid
        # leaking the override into other applications.
        previous = app.jinja_options.copy()
        try:
            app.jinja_options["autoescape"] = False
            with app.app_context():
                assert (
                    flask.render_template_string("{{ v }}", v=self.sample)
                    == self.sample
                )
        finally:
            app.jinja_options.clear()
            app.jinja_options.update(previous)

    def test_two_apps_with_different_policies_are_isolated(self, req_ctx):
        strict = flask.Flask(__name__)
        strict.config["JINJA_AUTOESCAPE_POLICY"] = "html"
        plain = flask.Flask(__name__)
        plain.config["JINJA_AUTOESCAPE_POLICY"] = "text"

        assert strict.jinja_env.autoescape is not plain.jinja_env.autoescape

        with strict.app_context():
            strict_output = flask.render_template_string("{{ v }}", v=self.sample)
        with plain.app_context():
            plain_output = flask.render_template_string("{{ v }}", v=self.sample)

        assert strict_output == self.escaped
        assert plain_output == self.sample

        # The global Jinja defaults were never modified.
        from jinja2 import Environment

        assert Environment().autoescape is False

    def test_streaming_chunking_is_stable(self, app, req_ctx):
        app.config["JINJA_AUTOESCAPE_POLICY"] = "html"
        source = "{% for i in range(3) %}{{ v }}-{{ i }};{% endfor %}"
        full = flask.render_template_string(source, v=self.sample)
        chunks = list(flask.stream_template_string(source, v=self.sample))

        assert "".join(chunks) == full
        for size in (1, 2, 4, 7, 1000):
            buf, grouped = "", []
            for chunk in chunks:
                buf += str(chunk)
                while len(buf) >= size:
                    grouped.append(buf[:size])
                    buf = buf[size:]
            grouped.append(buf)
            assert "".join(grouped) == full
