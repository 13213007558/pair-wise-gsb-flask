from __future__ import annotations

import collections.abc as cabc
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


#: File extensions that are treated as HTML by the selective policy.
_default_html_extensions: frozenset[str] = frozenset(
    (".html", ".htm", ".xml", ".xhtml", ".svg")
)


class AutoEscapePolicy:
    """Decide whether Jinja autoescaping is active when a template is
    compiled.

    A policy instance (or any other callable taking the template name)
    becomes the ``autoescape`` argument of the Jinja environment. Jinja
    calls it with the template's :attr:`~jinja2.Template.name` while
    compiling a template loaded by name, and with ``None`` for templates
    created from a source string (``render_template_string``). The
    returned value applies to the whole compiled template, including
    templates it extends, includes, or imports.

    Configuring a policy never changes Jinja globally. Each application
    creates its own :class:`~jinja2.Environment` with its own policy, so
    applications with different policies can run in the same process
    without affecting each other.

    Subclass this and override :meth:`should_escape` to implement custom
    behavior.
    """

    def should_escape(self, template_name: str | None) -> bool:
        raise NotImplementedError()

    def __call__(self, template_name: str | None) -> bool:
        return self.should_escape(template_name)


class HTMLAutoEscapePolicy(AutoEscapePolicy):
    """Escape every template unless its name uses a non-HTML extension.

    This is the strict policy. Templates named like ``mail.txt`` are not
    treated as HTML, but a template name supplied at run time (for
    example by a user) cannot disable escaping by choosing an extension,
    because any name without a known non-HTML extension is escaped too.
    Templates rendered from a source string (``template_name is None``)
    are always escaped.

    :param non_html_extensions: File extensions, including the dot, that
        identify plain-text output. Names are matched case-insensitively.
    """

    def __init__(
        self, non_html_extensions: cabc.Iterable[str] = (".txt", ".text")
    ) -> None:
        self.non_html_extensions = frozenset(
            extension.lower() for extension in non_html_extensions
        )

    def should_escape(self, template_name: str | None) -> bool:
        if template_name is None:
            return True

        return not template_name.lower().endswith(tuple(self.non_html_extensions))


class SelectiveAutoEscapePolicy(AutoEscapePolicy):
    """Escape only templates whose name uses an HTML extension.

    This preserves Flask's historical, extension-based behavior, which is
    the default. A name without a recognized HTML extension (or no name
    at all, such as a source string) is escaped as well; only names with
    a different extension, such as ``mail.txt``, are not escaped.
    """

    def should_escape(self, template_name: str | None) -> bool:
        if template_name is None:
            return True

        return template_name.lower().endswith(tuple(_default_html_extensions))


class TextAutoEscapePolicy(AutoEscapePolicy):
    """Never autoescape, for output that is not HTML such as plain-text
    email. This must be selected explicitly through configuration; it is
    never implied by a template name.

    Values marked with :class:`markupsafe.Markup` or the ``|safe``
    filter are still rendered unescaped, since both represent an explicit
    decision by the application to trust a value.
    """

    def should_escape(self, template_name: str | None) -> bool:
        return False


#: Policies selectable by name through the ``JINJA_AUTOESCAPE_POLICY``
#: config key.
_autoescape_policies: dict[str, type[AutoEscapePolicy]] = {
    "html": HTMLAutoEscapePolicy,
    "selective": SelectiveAutoEscapePolicy,
    "text": TextAutoEscapePolicy,
}


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

        for srcobj, loader in self._iter_loaders(template):
            try:
                rv = loader.get_source(environment, template)
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
        for _srcobj, loader in self._iter_loaders(template):
            try:
                return loader.get_source(environment, template)
            except TemplateNotFound:
                continue
        raise TemplateNotFound(template)

    def _iter_loaders(self, template: str) -> t.Iterator[tuple[Scaffold, BaseLoader]]:
        loader = self.app.jinja_loader
        if loader is not None:
            yield self.app, loader

        for blueprint in self.app.iter_blueprints():
            loader = blueprint.jinja_loader
            if loader is not None:
                yield blueprint, loader

    def list_templates(self) -> list[str]:
        result = set()
        loader = self.app.jinja_loader
        if loader is not None:
            result.update(loader.list_templates())

        for blueprint in self.app.iter_blueprints():
            loader = blueprint.jinja_loader
            if loader is not None:
                for template in loader.list_templates():
                    result.add(template)

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
