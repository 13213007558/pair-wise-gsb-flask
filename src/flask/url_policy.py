from __future__ import annotations

import typing as t

from .helpers import _split_blueprint_path

if t.TYPE_CHECKING:  # pragma: no cover
    from werkzeug.routing import MapAdapter

    from .sansio.app import App


class ExternalURLPolicyError(RuntimeError):
    """Raised when an external URL policy is selected but cannot be used.

    This happens when a policy name is selected through
    :data:`~flask.Flask.EXTERNAL_URL_POLICY` or a blueprint's
    ``external_url_policy`` argument, but the name is not defined in
    :data:`~flask.Flask.EXTERNAL_URL_POLICIES`, or the definition is
    invalid.

    The error is raised instead of silently falling back to the inbound
    request's ``Host`` header, which may be attacker controlled.

    .. versionadded:: 3.2
    """


def get_external_url_policy(
    app: App, endpoint: str, blueprint_name: str | None = None
) -> str | None:
    """Return the name of the external URL policy selected for ``endpoint``.

    If the endpoint starts with ``.`` it is first made relative to
    ``blueprint_name``. A policy set on the most specific blueprint that
    contains the endpoint takes precedence over a policy on a parent
    blueprint, which in turn takes precedence over the app-wide
    :data:`~flask.Flask.EXTERNAL_URL_POLICY`.

    Returns ``None`` when no policy is configured, in which case
    ``url_for`` keeps its standard behavior. Only configuration values
    are consulted; request parameters are never treated as a source.

    .. versionadded:: 3.2
    """
    if endpoint[:1] == ".":
        if blueprint_name is not None:
            endpoint = f"{blueprint_name}{endpoint}"
        else:
            endpoint = endpoint[1:]

    if "." in endpoint:
        blueprint_name = endpoint.rpartition(".")[0]

    if blueprint_name is not None:
        for name in _split_blueprint_path(blueprint_name):
            blueprint = app.blueprints.get(name)

            if blueprint is not None and blueprint.external_url_policy is not None:
                return blueprint.external_url_policy

    return app.config.get("EXTERNAL_URL_POLICY")


def make_policy_url_adapter(app: App, policy_name: str) -> MapAdapter:
    """Create a URL adapter bound to the configured external URL policy.

    The adapter is bound only to the scheme, host, and port from the
    app configuration. It never reads the active request, so the
    resulting external URLs are identical inside a request and in an app
    context such as a background task. The path is still built from the
    routing rules, with :data:`~flask.Flask.APPLICATION_ROOT` used as the
    root path.

    Raises :exc:`ExternalURLPolicyError` if the policy is not defined or
    does not provide a valid host, instead of falling back to the
    request's host.
    """
    policies = app.config.get("EXTERNAL_URL_POLICIES") or {}
    policy = policies.get(policy_name)

    if not isinstance(policy, dict):
        raise ExternalURLPolicyError(
            f"External URL policy {policy_name!r} is not defined in"
            " 'EXTERNAL_URL_POLICIES'."
        )

    host = policy.get("host")

    if not host or not isinstance(host, str):
        raise ExternalURLPolicyError(
            f"External URL policy {policy_name!r} must define a trusted"
            " 'host' string."
        )

    scheme = policy.get("scheme") or app.config["PREFERRED_URL_SCHEME"]
    port = policy.get("port")

    if port is not None:
        try:
            port = int(port)
        except (TypeError, ValueError) as error:
            raise ExternalURLPolicyError(
                f"External URL policy {policy_name!r} has an invalid"
                f" 'port' value: {port!r}."
            ) from error

        if not 1 <= port <= 65535:
            raise ExternalURLPolicyError(
                f"External URL policy {policy_name!r} port must be between"
                f" 1 and 65535, got {port}."
            )

        if ":" not in host:
            host = f"{host}:{port}"

    return app.url_map.bind(
        host,
        script_name=app.config["APPLICATION_ROOT"],
        url_scheme=scheme,
    )
