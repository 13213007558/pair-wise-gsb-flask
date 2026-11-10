from urllib.parse import urlsplit

import pytest

import flask
from flask import ExternalURLPolicyError


def make_app(**config):
    app = flask.Flask(__name__)
    app.config.update(config)

    @app.route("/reset/<token>")
    def reset(token):
        return token

    @app.route("/static-page")
    def static_page():
        return "ok"

    return app


def assert_url_matches_route(app, url, endpoint, **values):
    """Build a URL, then reverse-match its path back to the endpoint."""
    parts = urlsplit(url)
    bind_host = parts.hostname or ""

    if parts.port is not None:
        bind_host = f"{bind_host}:{parts.port}"

    adapter = app.url_map.bind(bind_host, script_name="/")
    matched_endpoint, matched_values = adapter.match(parts.path)
    assert matched_endpoint == endpoint
    assert all(
        matched_values.get(key) in (value, str(value)) for key, value in values.items()
    )


class TestAppWidePolicy:
    def test_policy_overrides_inbound_host(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {"scheme": "https", "host": "mail.example.com"}
            },
        )

        with app.test_request_context("/reset/tok", base_url="http://evil.example.net"):
            url = flask.url_for("reset", token="tok", _external=True)

        assert url == "https://mail.example.com/reset/tok"
        assert "evil.example.net" not in url

    def test_same_result_in_request_and_app_context(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {"scheme": "https", "host": "mail.example.com"}
            },
        )

        with app.test_request_context("/", base_url="http://evil.example.net"):
            request_url = flask.url_for("reset", token="tok", _external=True)

        with app.app_context():
            app_context_url = flask.url_for("reset", token="tok")

        assert request_url == app_context_url == ("https://mail.example.com/reset/tok")

    def test_relative_urls_are_unchanged(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {"scheme": "https", "host": "mail.example.com"}
            },
        )

        with app.test_request_context("/", base_url="http://evil.example.net"):
            assert flask.url_for("reset", token="tok") == "/reset/tok"
            assert flask.url_for("reset", token="tok", _external=False) == (
                "/reset/tok"
            )

    def test_non_default_port(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {
                    "scheme": "https",
                    "host": "mail.example.com",
                    "port": 8443,
                }
            },
        )

        with app.app_context():
            url = flask.url_for("reset", token="tok")

        assert url == "https://mail.example.com:8443/reset/tok"
        assert_url_matches_route(app, url, "reset", token="tok")

    def test_explicit_scheme_overrides_scheme_only(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {"scheme": "https", "host": "mail.example.com"}
            },
        )

        with app.app_context():
            url = flask.url_for("reset", token="tok", _scheme="http")

        assert url == "http://mail.example.com/reset/tok"

    def test_application_root_is_kept(self):
        app = make_app(
            APPLICATION_ROOT="/app",
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {"scheme": "https", "host": "mail.example.com"}
            },
        )

        with app.app_context():
            url = flask.url_for("reset", token="tok")

        assert url == "https://mail.example.com/app/reset/tok"

    def test_path_and_query_are_preserved_and_reverse_match(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {"scheme": "https", "host": "mail.example.com"}
            },
        )

        with app.app_context():
            url = flask.url_for("static_page", _external=True)

        assert url == "https://mail.example.com/static-page"
        assert_url_matches_route(app, url, "static_page")

    def test_similar_hostname_is_not_confused(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={"email": {"scheme": "https", "host": "example.com"}},
        )

        with app.test_request_context("/", base_url="http://example.com.evil.test"):
            url = flask.url_for("reset", token="tok", _external=True)

        assert url == "https://example.com/reset/tok"
        assert urlsplit(url).hostname == "example.com"


class TestBlueprintPolicy:
    def test_blueprint_policy_is_used_for_its_endpoints(self):
        app = flask.Flask(__name__)
        bp = flask.Blueprint("auth", __name__, external_url_policy="email")

        @bp.route("/reset/<token>")
        def reset(token):
            return token

        @app.route("/home")
        def home():
            return "home"

        app.register_blueprint(bp)
        app.config["EXTERNAL_URL_POLICIES"] = {
            "email": {"scheme": "https", "host": "mail.example.com"},
            "web": {"scheme": "https", "host": "www.example.com"},
        }

        with app.test_request_context("/", base_url="http://evil.example.net"):
            assert flask.url_for("auth.reset", token="tok", _external=True) == (
                "https://mail.example.com/reset/tok"
            )
            # No policy on the app or this blueprint: request host used.
            assert flask.url_for("home", _external=True) == (
                "http://evil.example.net/home"
            )

    def test_nested_blueprint_takes_precedence(self):
        app = flask.Flask(__name__)
        parent = flask.Blueprint(
            "parent", __name__, external_url_policy="parent_origin"
        )
        child = flask.Blueprint("child", __name__, external_url_policy="child_origin")

        @child.route("/reset/<token>")
        def reset(token):
            return token

        parent.register_blueprint(child)
        app.register_blueprint(parent)
        app.config["EXTERNAL_URL_POLICIES"] = {
            "parent_origin": {
                "scheme": "https",
                "host": "parent.example.com",
            },
            "child_origin": {
                "scheme": "https",
                "host": "child.example.com",
            },
        }

        with app.app_context():
            url = flask.url_for("parent.child.reset", token="tok")

        assert url == "https://child.example.com/reset/tok"

    def test_app_default_used_when_blueprint_has_no_policy(self):
        app = flask.Flask(__name__)
        bp = flask.Blueprint("auth", __name__)

        @bp.route("/reset/<token>")
        def reset(token):
            return token

        app.register_blueprint(bp)
        app.config["EXTERNAL_URL_POLICY"] = "email"
        app.config["EXTERNAL_URL_POLICIES"] = {
            "email": {"scheme": "https", "host": "mail.example.com"}
        }

        with app.test_request_context("/", base_url="http://evil.example.net"):
            assert flask.url_for("auth.reset", token="tok", _external=True) == (
                "https://mail.example.com/reset/tok"
            )

    def test_relative_blueprint_endpoint_uses_policy(self):
        app = flask.Flask(__name__)
        bp = flask.Blueprint("auth", __name__, external_url_policy="email")

        @bp.route("/reset/<token>")
        def reset(token):
            return token

        app.register_blueprint(bp)
        app.config["EXTERNAL_URL_POLICIES"] = {
            "email": {"scheme": "https", "host": "mail.example.com"}
        }

        with app.test_request_context("/reset/old", base_url="http://evil.example.net"):
            assert flask.url_for(".reset", token="tok", _external=True) == (
                "https://mail.example.com/reset/tok"
            )


class TestRouteBuilding:
    def test_subdomain_is_prepended_to_policy_host(self):
        app = flask.Flask(__name__, subdomain_matching=True)
        app.config["SERVER_NAME"] = "example.com"
        app.config["EXTERNAL_URL_POLICY"] = "email"
        app.config["EXTERNAL_URL_POLICIES"] = {
            "email": {"scheme": "https", "host": "mail.example.com"}
        }

        @app.route("/", subdomain="api")
        def api_index():
            return ""

        with app.app_context():
            url = flask.url_for("api_index", _external=True)

        assert url == "https://api.mail.example.com/"
        parts = urlsplit(url)
        assert parts.scheme == "https"
        assert parts.hostname == "api.mail.example.com"
        assert parts.path == "/"

    def test_default_scheme_falls_back_to_preferred_scheme(self):
        app = make_app(
            PREFERRED_URL_SCHEME="https",
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={"email": {"host": "mail.example.com"}},
        )

        with app.app_context():
            url = flask.url_for("static_page")

        assert url == "https://mail.example.com/static-page"

    def test_host_with_port_in_policy_is_respected(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {"scheme": "https", "host": "mail.example.com:9000"}
            },
        )

        with app.app_context():
            url = flask.url_for("static_page", port=None)

        assert url == "https://mail.example.com:9000/static-page"

    def test_port_ignored_when_host_already_has_port(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {
                    "scheme": "https",
                    "host": "mail.example.com:9000",
                    "port": 8443,
                }
            },
        )

        with app.app_context():
            url = flask.url_for("static_page")

        assert url == "https://mail.example.com:9000/static-page"

    def test_static_endpoint_and_anchor_use_policy(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {"scheme": "https", "host": "mail.example.com"}
            },
        )

        with app.app_context():
            url = flask.url_for(
                "static", filename="app.css", _anchor="bundle", _external=True
            )

        assert url == "https://mail.example.com/static/app.css#bundle"
        assert_url_matches_route(app, url, "static", filename="app.css")

    def test_policy_adapter_uses_current_configuration(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {"scheme": "https", "host": "mail.example.com"}
            },
        )

        with app.app_context():
            first = flask.url_for("static_page", _external=True)

        app.config["EXTERNAL_URL_POLICIES"]["email"]["host"] = "other.example.com"

        with app.app_context():
            second = flask.url_for("static_page", _external=True)

        assert first == "https://mail.example.com/static-page"
        assert second == "https://other.example.com/static-page"


class TestTrustedMultiDomain:
    def test_multiple_policies_select_by_blueprint(self):
        app = flask.Flask(__name__)
        auth = flask.Blueprint("auth", __name__, external_url_policy="auth_origin")
        shop = flask.Blueprint("shop", __name__, external_url_policy="shop_origin")

        @auth.route("/reset/<token>")
        def auth_reset(token):
            return token

        @shop.route("/order/<int:order_id>")
        def shop_order(order_id):
            return str(order_id)

        app.register_blueprint(auth)
        app.register_blueprint(shop)
        app.config["EXTERNAL_URL_POLICIES"] = {
            "auth_origin": {
                "scheme": "https",
                "host": "auth.example.com",
            },
            "shop_origin": {
                "scheme": "https",
                "host": "shop.example.com",
                "port": 8443,
            },
        }

        with app.test_request_context("/", base_url="http://evil.example.net"):
            auth_url = flask.url_for("auth.auth_reset", token="tok", _external=True)
            shop_url = flask.url_for("shop.shop_order", order_id=7, _external=True)

        assert auth_url == "https://auth.example.com/reset/tok"
        assert shop_url == "https://shop.example.com:8443/order/7"
        assert_url_matches_route(app, shop_url, "shop.shop_order", order_id=7)

    def test_request_parameters_are_never_used_as_origin(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {"scheme": "https", "host": "mail.example.com"}
            },
        )

        with app.test_request_context(
            "/?host=https://attacker.test&scheme=ftp",
            base_url="http://attacker.test",
        ):
            url = flask.url_for(
                "reset",
                token="tok",
                host="https://attacker.test",
                scheme="ftp",
                _external=True,
            )

        assert url.startswith("https://mail.example.com/reset/tok?")
        assert "attacker.test" in url  # only as an encoded query value
        assert urlsplit(url).hostname == "mail.example.com"


class TestFailures:
    def test_missing_policy_definition_raises(self):
        app = make_app(EXTERNAL_URL_POLICY="missing")

        with app.app_context():
            with pytest.raises(ExternalURLPolicyError, match="not defined"):
                flask.url_for("reset", token="tok")

    def test_missing_policy_does_not_fall_back_to_host(self):
        app = make_app(EXTERNAL_URL_POLICY="missing")

        with app.test_request_context("/", base_url="http://evil.example.net"):
            with pytest.raises(ExternalURLPolicyError):
                flask.url_for("reset", token="tok", _external=True)

            # Relative URLs still work and never include the host.
            assert flask.url_for("reset", token="tok") == "/reset/tok"

    def test_missing_host_in_policy_raises(self):
        app = make_app(
            EXTERNAL_URL_POLICY="broken",
            EXTERNAL_URL_POLICIES={"broken": {"scheme": "https"}},
        )

        with app.app_context():
            with pytest.raises(ExternalURLPolicyError, match="'host'"):
                flask.url_for("reset", token="tok")

    def test_invalid_port_raises(self):
        app = make_app(
            EXTERNAL_URL_POLICY="broken",
            EXTERNAL_URL_POLICIES={
                "broken": {"host": "mail.example.com", "port": 70000}
            },
        )

        with app.app_context():
            with pytest.raises(ExternalURLPolicyError, match="port"):
                flask.url_for("reset", token="tok")

    def test_unknown_blueprint_policy_raises(self):
        app = flask.Flask(__name__)
        bp = flask.Blueprint("auth", __name__, external_url_policy="missing")
        app.config["EXTERNAL_URL_POLICIES"] = {"other": {"host": "mail.example.com"}}

        @bp.route("/reset")
        def reset():
            return "ok"

        app.register_blueprint(bp)

        with app.app_context():
            with pytest.raises(ExternalURLPolicyError):
                flask.url_for("auth.reset")

    def test_no_server_name_without_policy_still_errors(self):
        app = make_app()

        with app.app_context():
            with pytest.raises(RuntimeError, match="SERVER_NAME"):
                flask.url_for("reset", token="tok")


class TestDisabledByDefault:
    def test_standard_behavior_without_policy(self):
        app = make_app()

        with app.test_request_context("/", base_url="http://localhost:5000"):
            assert flask.url_for("reset", token="tok") == "/reset/tok"
            assert flask.url_for("reset", token="tok", _external=True) == (
                "http://localhost:5000/reset/tok"
            )

    def test_external_without_policy_uses_server_name_outside_request(self):
        app = make_app(
            SERVER_NAME="example.com",
            PREFERRED_URL_SCHEME="https",
        )

        with app.app_context():
            assert flask.url_for("reset", token="tok") == (
                "https://example.com/reset/tok"
            )

    def test_build_error_handler_still_invoked(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {"scheme": "https", "host": "mail.example.com"}
            },
        )

        @app.url_build_error_handlers.append
        def handle(error, endpoint, values):
            return f"https://fallback.example.com/missing/{endpoint}"

        with app.app_context():
            assert flask.url_for("does.not.exist") == (
                "https://fallback.example.com/missing/does.not.exist"
            )

    def test_scheme_without_external_still_errors(self):
        app = make_app(
            EXTERNAL_URL_POLICY="email",
            EXTERNAL_URL_POLICIES={
                "email": {"scheme": "https", "host": "mail.example.com"}
            },
        )

        with app.test_request_context("/"):
            with pytest.raises(ValueError, match="_external"):
                flask.url_for("reset", token="tok", _scheme="https", _external=False)
