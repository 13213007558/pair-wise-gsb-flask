from __future__ import annotations

import typing as t

from jinja2 import BaseLoader
from jinja2 import Environment as BaseEnvironment
from jinja2 import Template
from jinja2 import TemplateNotFound

from .ctx import AppContext
from .globals import app_ctx
from .helpers import stream_with_context
from .signals import before_render_template
from .signals import template_rendered

if t.TYPE_CHECKING:  # pragma: no cover
    from .sansio.app import App
    from .sansio.scaffold import Scaffold

#: Separator between a blueprint's registered name and the template path in
#: an explicitly namespaced template name, such as ``"admin::index.html"``.
TEMPLATE_NAMESPACE_SEPARATOR = "::"

#: Prefix within the application's template folder where per-blueprint
#: overrides live, for example ``templates/overrides/admin/index.html``.
TEMPLATE_OVERRIDE_PREFIX = "overrides"


def _default_template_ctx_processor() -> dict[str, t.Any]:
    """Default template context processor.  Replaces the ``request`` and ``g``
    proxies with their concrete objects for faster access.
    """
    ctx = app_ctx._get_current_object()
    rv: dict[str, t.Any] = {"g": ctx.g}

    if ctx.has_request:
        rv["request"] = ctx.request
        # The session proxy cannot be replaced, accessing it gets
        # RequestContext.session, which sets session.accessed.

    return rv


class Environment(BaseEnvironment):
    """Works like a regular Jinja environment but has some additional
    knowledge of how Flask's blueprint works so that it can prepend the
    name of the blueprint to referenced templates if necessary.
    """

    def __init__(self, app: App, **options: t.Any) -> None:
        if "loader" not in options:
            options["loader"] = app.create_global_jinja_loader()
        BaseEnvironment.__init__(self, **options)
        self.app = app

    def join_path(self, template: str, parent: str) -> str:
        """Resolve a template name referenced from ``parent``.

        If ``parent`` was loaded through a template namespace such as
        ``"report::page.html"``, an unqualified reference is resolved
        within the same namespace. An already qualified reference is used
        as-is, allowing an explicit jump to another namespace. Unqualified
        names with an unqualified parent keep the default global lookup.

        .. versionadded:: 3.2
        """
        if TEMPLATE_NAMESPACE_SEPARATOR in template:
            return template

        namespace, sep, _ = parent.partition(TEMPLATE_NAMESPACE_SEPARATOR)

        if sep:
            return f"{namespace}{sep}{template}"

        return template


class DispatchingJinjaLoader(BaseLoader):
    """A loader that looks for templates in the application and all
    the blueprint folders.

    A template name may be qualified with a blueprint's registered name
    using the syntax ``"name::path/to/template"``. In that case only the
    application's override folder
    (``templates/overrides/<name>/<path>``) and that blueprint's own
    template folder are searched. Unqualified names keep the historical
    behavior: the application first, then every blueprint in
    registration order.
    """

    def __init__(self, app: App) -> None:
        self.app = app

    def get_source(
        self, environment: BaseEnvironment, template: str
    ) -> tuple[str, str | None, t.Callable[[], bool] | None]:
        if self.app.config["EXPLAIN_TEMPLATE_LOADING"]:
            return self._get_source_explained(environment, template)
        return self._get_source_fast(environment, template)

    def _get_source_explained(
        self, environment: BaseEnvironment, template: str
    ) -> tuple[str, str | None, t.Callable[[], bool] | None]:
        attempts = []
        rv: tuple[str, str | None, t.Callable[[], bool] | None] | None
        trv: None | (tuple[str, str | None, t.Callable[[], bool] | None]) = None

        for srcobj, loader, name in self._iter_loaders(template):
            try:
                rv = loader.get_source(environment, name)
                if trv is None:
                    trv = rv
            except TemplateNotFound:
                rv = None
            attempts.append((loader, srcobj, name, rv))

        from .debughelpers import explain_template_loading_attempts

        explain_template_loading_attempts(self.app, template, attempts)

        if trv is not None:
            return trv
        raise TemplateNotFound(template)

    def _get_source_fast(
        self, environment: BaseEnvironment, template: str
    ) -> tuple[str, str | None, t.Callable[[], bool] | None]:
        for _srcobj, loader, name in self._iter_loaders(template):
            try:
                return loader.get_source(environment, name)
            except TemplateNotFound:
                continue
        raise TemplateNotFound(template)

    def _iter_loaders(
        self, template: str
    ) -> t.Iterator[tuple[Scaffold, BaseLoader, str]]:
        """Yield ``(source, loader, name)`` triples to try, in order.

        ``name`` is the template name to pass to that loader, which may
        differ from the requested ``template`` for namespaced lookups.
        """
        namespace, sep, path = template.partition(TEMPLATE_NAMESPACE_SEPARATOR)

        if sep:
            blueprint = self.app.blueprints.get(namespace)

            if blueprint is None:
                return

            loader = self.app.jinja_loader
            if loader is not None:
                override = "/".join((TEMPLATE_OVERRIDE_PREFIX, namespace, path))
                yield self.app, loader, override

            loader = blueprint.jinja_loader
            if loader is not None:
                yield blueprint, loader, path

            return

        loader = self.app.jinja_loader
        if loader is not None:
            yield self.app, loader, template

        for blueprint in self.app.iter_blueprints():
            loader = blueprint.jinja_loader
            if loader is not None:
                yield blueprint, loader, template

    def list_templates(self) -> list[str]:
        result = set()
        app_templates: set[str] = set()
        loader = self.app.jinja_loader
        if loader is not None:
            app_templates = set(loader.list_templates())
            result.update(app_templates)

        for name, blueprint in self.app.blueprints.items():
            loader = blueprint.jinja_loader
            if loader is not None:
                for template in loader.list_templates():
                    result.add(template)
                    result.add(f"{name}{TEMPLATE_NAMESPACE_SEPARATOR}{template}")

        override_prefix = f"{TEMPLATE_OVERRIDE_PREFIX}/"

        for template in app_templates:
            if template.startswith(override_prefix):
                namespace, _, path = template[len(override_prefix) :].partition("/")

                if path and namespace in self.app.blueprints:
                    result.add(f"{namespace}{TEMPLATE_NAMESPACE_SEPARATOR}{path}")

        return list(result)


def _render(ctx: AppContext, template: Template, context: dict[str, t.Any]) -> str:
    app = ctx.app
    app.update_template_context(ctx, context)
    before_render_template.send(
        app, _async_wrapper=app.ensure_sync, template=template, context=context
    )
    rv = template.render(context)
    template_rendered.send(
        app, _async_wrapper=app.ensure_sync, template=template, context=context
    )
    return rv


def render_template(
    template_name_or_list: str | Template | list[str | Template],
    **context: t.Any,
) -> str:
    """Render a template by name with the given context.

    :param template_name_or_list: The name of the template to render. If
        a list is given, the first name to exist will be rendered.
    :param context: The variables to make available in the template.
    """
    ctx = app_ctx._get_current_object()
    template = ctx.app.jinja_env.get_or_select_template(template_name_or_list)
    return _render(ctx, template, context)


def render_template_string(source: str, **context: t.Any) -> str:
    """Render a template from the given source string with the given
    context.

    :param source: The source code of the template to render.
    :param context: The variables to make available in the template.
    """
    ctx = app_ctx._get_current_object()
    template = ctx.app.jinja_env.from_string(source)
    return _render(ctx, template, context)


def _stream(
    ctx: AppContext, template: Template, context: dict[str, t.Any]
) -> t.Iterator[str]:
    app = ctx.app
    app.update_template_context(ctx, context)
    before_render_template.send(
        app, _async_wrapper=app.ensure_sync, template=template, context=context
    )

    def generate() -> t.Iterator[str]:
        yield from template.generate(context)
        template_rendered.send(
            app, _async_wrapper=app.ensure_sync, template=template, context=context
        )

    return stream_with_context(generate())


def stream_template(
    template_name_or_list: str | Template | list[str | Template],
    **context: t.Any,
) -> t.Iterator[str]:
    """Render a template by name with the given context as a stream.
    This returns an iterator of strings, which can be used as a
    streaming response from a view.

    :param template_name_or_list: The name of the template to render. If
        a list is given, the first name to exist will be rendered.
    :param context: The variables to make available in the template.

    .. versionadded:: 2.2
    """
    ctx = app_ctx._get_current_object()
    template = ctx.app.jinja_env.get_or_select_template(template_name_or_list)
    return _stream(ctx, template, context)


def stream_template_string(source: str, **context: t.Any) -> t.Iterator[str]:
    """Render a template from the given source string with the given
    context as a stream. This returns an iterator of strings, which can
    be used as a streaming response from a view.

    :param source: The source code of the template to render.
    :param context: The variables to make available in the template.

    .. versionadded:: 2.2
    """
    ctx = app_ctx._get_current_object()
    template = ctx.app.jinja_env.from_string(source)
    return _stream(ctx, template, context)
