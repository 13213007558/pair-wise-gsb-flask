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


#: Separates the blueprint namespace from the template name in an
#: explicitly namespaced template name, such as "report::page.html".
_namespace_separator = "::"

#: Prefix within the application's template folder that is searched first
#: when resolving a namespaced template, allowing the application to
#: override a blueprint's templates.
_override_prefix = "overrides"


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
        """Resolve a template referenced by the "parent" template. If the
        parent was loaded from a blueprint namespace ("name::template"), an
        unqualified reference is resolved within the same namespace. A
        reference that already contains "::" is used unchanged, so a
        template can explicitly jump to another namespace.

        .. versionadded:: 3.2
        """
        if _namespace_separator in template or _namespace_separator not in parent:
            return template

        namespace = parent.partition(_namespace_separator)[0]
        return f"{namespace}{_namespace_separator}{template}"


class DispatchingJinjaLoader(BaseLoader):
    """A loader that looks for templates in the application and all
    the blueprint folders.
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
            attempts.append((loader, srcobj, rv))

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
    ) -> t.Iterator[tuple[Scaffold | str, BaseLoader, str]]:
        """Yield "(srcobj, loader, name)" for each loader to try, in
        order. "name" is the template name to pass to that loader, which
        may differ from "template" for namespaced lookups.
        """
        namespace, sep, name = template.partition(_namespace_separator)

        if sep:
            yield from self._iter_namespaced_loaders(namespace, name)
            return

        loader = self.app.jinja_loader
        if loader is not None:
            yield self.app, loader, template

        for blueprint in self.app.iter_blueprints():
            loader = blueprint.jinja_loader
            if loader is not None:
                yield blueprint, loader, template

    def _iter_namespaced_loaders(
        self, namespace: str, name: str
    ) -> t.Iterator[tuple[Scaffold | str, BaseLoader, str]]:
        """Yield the loaders for a "namespace::name" template. The
        application's override folder is searched first, then the
        blueprint registered under "namespace". The namespace is the
        blueprint's full registered name, so nested registrations such as
        "admin.report" and multiple registrations of one blueprint are
        distinct namespaces.
        """
        loader = self.app.jinja_loader

        if loader is not None:
            yield (
                f"application override for namespace {namespace!r}",
                loader,
                f"{_override_prefix}/{namespace}/{name}",
            )

        blueprint = self.app.blueprints.get(namespace)

        if blueprint is not None:
            loader = blueprint.jinja_loader

            if loader is not None:
                yield blueprint, loader, name

    def list_templates(self) -> list[str]:
        result = set()
        loader = self.app.jinja_loader
        if loader is not None:
            result.update(loader.list_templates())

        for name, blueprint in self.app.blueprints.items():
            loader = blueprint.jinja_loader
            if loader is not None:
                for template in loader.list_templates():
                    result.add(template)
                    result.add(f"{name}{_namespace_separator}{template}")

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
