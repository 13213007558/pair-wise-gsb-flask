import logging
import os

import pytest
from jinja2 import FileSystemBytecodeCache
from jinja2 import TemplateNotFound

import flask


def _write(folder, name, source):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)


def _write_plugin(folder, label):
    _write(
        folder,
        "layout.html",
        label + "-LAYOUT[{% block content %}{% endblock %}]",
    )
    _write(
        folder,
        "page.html",
        "{% extends 'layout.html' %}{% block content %}"
        + label
        + "-PAGE {% include '_card.html' %}{% endblock %}",
    )
    _write(folder, "_card.html", label + "-CARD")
    _write(
        folder, "macros.html", "{% macro hello() %}" + label + "-MACRO{% endmacro %}"
    )
    _write(folder, "uses_macros.html", "{% import 'macros.html' as m %}{{ m.hello() }}")


def _make_app(tmp_path, name="ns_test_app"):
    app_dir = tmp_path / "app_templates"
    report_dir = tmp_path / "report_templates"
    blog_dir = tmp_path / "blog_templates"

    _write(app_dir, "page.html", "APP-PAGE")
    _write_plugin(report_dir, "REPORT")
    _write_plugin(blog_dir, "BLOG")
    _write(report_dir, "uses_blog.html", "REPORT {% include 'blog::_card.html' %}")
    _write(
        blog_dir,
        "extends_report.html",
        "{% extends 'report::layout.html' %}"
        "{% block content %}BLOG-IN-REPORT{% endblock %}",
    )

    app = flask.Flask(name, template_folder=str(app_dir))
    app.config["TESTING"] = True
    app.register_blueprint(
        flask.Blueprint("report", __name__, template_folder=str(report_dir))
    )
    app.register_blueprint(
        flask.Blueprint("blog", __name__, template_folder=str(blog_dir))
    )
    return app


@pytest.fixture
def ns_app(tmp_path):
    return _make_app(tmp_path)


def test_qualified_names_select_distinct_templates(ns_app):
    with ns_app.app_context():
        report = flask.render_template("report::page.html")
        blog = flask.render_template("blog::page.html")

    assert report == "REPORT-LAYOUT[REPORT-PAGE REPORT-CARD]"
    assert blog == "BLOG-LAYOUT[BLOG-PAGE BLOG-CARD]"


def test_relative_references_stay_in_namespace(ns_app):
    # extends, include, and import without a qualifier resolve within the
    # namespace of the template that references them.
    with ns_app.app_context():
        assert flask.render_template("report::uses_macros.html") == "REPORT-MACRO"
        assert flask.render_template("blog::uses_macros.html") == "BLOG-MACRO"


def test_explicit_cross_namespace_reference(ns_app):
    with ns_app.app_context():
        assert flask.render_template("report::uses_blog.html") == "REPORT BLOG-CARD"
        assert (
            flask.render_template("blog::extends_report.html")
            == "REPORT-LAYOUT[BLOG-IN-REPORT]"
        )


def test_app_override_keeps_namespace(tmp_path):
    app = _make_app(tmp_path)
    _write(
        tmp_path / "app_templates" / "overrides" / "report",
        "page.html",
        "{% extends 'layout.html' %}{% block content %}"
        "OVERRIDE {% include '_card.html' %}{% endblock %}",
    )

    with app.app_context():
        # The override wins, and its relative references still resolve in
        # the overridden blueprint's namespace.
        assert flask.render_template("report::page.html") == (
            "REPORT-LAYOUT[OVERRIDE REPORT-CARD]"
        )
        assert flask.render_template("blog::page.html") == (
            "BLOG-LAYOUT[BLOG-PAGE BLOG-CARD]"
        )


def test_bare_names_keep_global_lookup(ns_app):
    with ns_app.app_context():
        # The application still wins over blueprints for bare names.
        assert flask.render_template("page.html") == "APP-PAGE"
        # A bare name only found in blueprints uses registration order.
        assert flask.render_template("layout.html") == "REPORT-LAYOUT[]"
        assert flask.render_template("_card.html") == "REPORT-CARD"


def test_nested_and_repeated_registration(tmp_path):
    app = _make_app(tmp_path)
    report_dir = tmp_path / "report_templates"

    # The same template folder registered again under a different name.
    app.register_blueprint(
        flask.Blueprint("again", __name__, template_folder=str(report_dir)),
        name="report2",
    )

    # A blueprint nested inside another one gets a dotted name.
    admin = flask.Blueprint("admin", __name__)
    admin.register_blueprint(
        flask.Blueprint("report", __name__, template_folder=str(report_dir))
    )
    app.register_blueprint(admin)

    with app.app_context():
        assert flask.render_template("report::page.html") == (
            "REPORT-LAYOUT[REPORT-PAGE REPORT-CARD]"
        )
        assert flask.render_template("report2::page.html") == (
            "REPORT-LAYOUT[REPORT-PAGE REPORT-CARD]"
        )
        assert flask.render_template("admin.report::page.html") == (
            "REPORT-LAYOUT[REPORT-PAGE REPORT-CARD]"
        )

    names = set(app.jinja_env.list_templates())
    assert "report2::page.html" in names
    assert "admin.report::page.html" in names


def test_cache_warmup_then_interleaved_rendering(ns_app):
    names = ("report::page.html", "blog::page.html")

    with ns_app.app_context():
        # Warm up the template cache.
        expected = {name: flask.render_template(name) for name in names}

        cached_names = {key[1] for key in ns_app.jinja_env.cache}
        assert "report::page.html" in cached_names
        assert "blog::page.html" in cached_names

        # Interleave renders; cached templates must not leak across spaces.
        for _ in range(3):
            for name in names:
                assert flask.render_template(name) == expected[name]

    assert expected["report::page.html"] != expected["blog::page.html"]


def test_bytecode_cache_uses_qualified_identity(tmp_path):
    cache_dir = tmp_path / "bytecode"
    cache_dir.mkdir()
    bytecode_cache = FileSystemBytecodeCache(str(cache_dir))

    app = _make_app(tmp_path / "src")
    app.jinja_options = {**app.jinja_options, "bytecode_cache": bytecode_cache}

    with app.app_context():
        assert flask.render_template("report::page.html") == (
            "REPORT-LAYOUT[REPORT-PAGE REPORT-CARD]"
        )
        assert flask.render_template("blog::page.html") == (
            "BLOG-LAYOUT[BLOG-PAGE BLOG-CARD]"
        )

    assert any(cache_dir.iterdir())

    # A fresh environment sharing the same bytecode cache directory must
    # still render each namespace correctly.
    other = _make_app(tmp_path / "src", name="ns_test_app_2")
    other.jinja_options = {**other.jinja_options, "bytecode_cache": bytecode_cache}

    with other.app_context():
        assert flask.render_template("report::page.html") == (
            "REPORT-LAYOUT[REPORT-PAGE REPORT-CARD]"
        )
        assert flask.render_template("blog::page.html") == (
            "BLOG-LAYOUT[BLOG-PAGE BLOG-CARD]"
        )


def test_auto_reload_uses_qualified_identity(tmp_path):
    app = _make_app(tmp_path)
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    with app.app_context():
        assert flask.render_template("report::page.html") == (
            "REPORT-LAYOUT[REPORT-PAGE REPORT-CARD]"
        )

        page = tmp_path / "report_templates" / "page.html"
        page.write_text(
            "{% extends 'layout.html' %}{% block content %}REPORT-V2{% endblock %}"
        )
        mtime = os.path.getmtime(page) + 5
        os.utime(page, (mtime, mtime))

        assert flask.render_template("report::page.html") == "REPORT-LAYOUT[REPORT-V2]"
        # The other namespace's cached template is unaffected.
        assert flask.render_template("blog::page.html") == (
            "BLOG-LAYOUT[BLOG-PAGE BLOG-CARD]"
        )


def test_list_templates_includes_qualified_names(ns_app, tmp_path):
    _write(
        tmp_path / "app_templates" / "overrides" / "report",
        "extra.html",
        "EXTRA",
    )

    names = set(ns_app.jinja_env.list_templates())
    # Bare names keep the historical behavior.
    assert "page.html" in names
    # Qualified names are listed for every registered blueprint.
    assert "report::page.html" in names
    assert "report::layout.html" in names
    assert "blog::page.html" in names
    # Templates that only exist as an application override are listed too.
    assert "report::extra.html" in names

    with ns_app.app_context():
        assert flask.render_template("report::extra.html") == "EXTRA"


def test_unknown_namespace_raises_not_found(ns_app):
    with ns_app.app_context():
        with pytest.raises(TemplateNotFound) as exc_info:
            flask.render_template("ghost::page.html")

    assert "ghost::page.html" in str(exc_info.value)


def test_explain_loading_does_not_change_selection(ns_app, monkeypatch):
    with ns_app.app_context():
        expected_report = flask.render_template("report::page.html")
        expected_blog = flask.render_template("blog::page.html")

    monkeypatch.setitem(ns_app.config, "EXPLAIN_TEMPLATE_LOADING", True)
    ns_app.jinja_env.cache.clear()

    with ns_app.app_context():
        assert flask.render_template("report::page.html") == expected_report
        assert flask.render_template("blog::page.html") == expected_blog


def test_explain_loading_shows_searched_namespaces(ns_app, caplog):
    ns_app.config["EXPLAIN_TEMPLATE_LOADING"] = True

    with caplog.at_level(logging.INFO, logger="ns_test_app"):
        with ns_app.app_context():
            with pytest.raises(TemplateNotFound):
                flask.render_template("report::missing.html")

    text = caplog.text
    assert "Locating template 'report::missing.html':" in text
    assert "trying loader of application 'ns_test_app'" in text
    assert "looking up 'overrides/report/missing.html'" in text
    assert "trying loader of blueprint 'report'" in text
    # Only the requested namespace was searched, not every blueprint.
    assert "blueprint 'blog'" not in text
    assert "Error: the template could not be found" in text

    caplog.clear()

    with caplog.at_level(logging.INFO, logger="ns_test_app"):
        with ns_app.app_context():
            with pytest.raises(TemplateNotFound):
                flask.render_template("ghost::missing.html")

    text = caplog.text
    assert "Locating template 'ghost::missing.html':" in text
    assert "Error: the template could not be found" in text
