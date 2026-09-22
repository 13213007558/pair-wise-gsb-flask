from __future__ import annotations

import typing as t

from werkzeug.exceptions import BadRequest
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Request as RequestBase
from werkzeug.wrappers import Response as ResponseBase

from . import json
from .globals import current_app
from .helpers import _split_blueprint_path
from .json_seq import iter_json_seq as _iter_json_seq
from .json_seq import JsonSeqError

if t.TYPE_CHECKING:  # pragma: no cover
    from werkzeug.routing import Rule


class Request(RequestBase):
    """The request object used by default in Flask.  Remembers the
    matched endpoint and view arguments.

    It is what ends up as :class:`~flask.request`.  If you want to replace
    the request object used you can subclass this and set
    :attr:`~flask.Flask.request_class` to your subclass.

    The request object is a :class:`~werkzeug.wrappers.Request` subclass and
    provides all of the attributes Werkzeug defines plus a few Flask
    specific ones.
    """

    json_module: t.Any = json

    #: The internal URL rule that matched the request.  This can be
    #: useful to inspect which methods are allowed for the URL from
    #: a before/after handler (``request.url_rule.methods``) etc.
    #: Though if the request's method was invalid for the URL rule,
    #: the valid list is available in ``routing_exception.valid_methods``
    #: instead (an attribute of the Werkzeug exception
    #: :exc:`~werkzeug.exceptions.MethodNotAllowed`)
    #: because the request was never internally bound.
    #:
    #: .. versionadded:: 0.6
    url_rule: Rule | None = None

    #: A dict of view arguments that matched the request.  If an exception
    #: happened when matching, this will be ``None``.
    view_args: dict[str, t.Any] | None = None

    #: If matching the URL failed, this is the exception that will be
    #: raised / was raised as part of the request handling.  This is
    #: usually a :exc:`~werkzeug.exceptions.NotFound` exception or
    #: something similar.
    routing_exception: HTTPException | None = None

    _max_content_length: int | None = None
    _max_form_memory_size: int | None = None
    _max_form_parts: int | None = None
    _json_seq_iterating = False

    @property
    def max_content_length(self) -> int | None:
        """The maximum number of bytes that will be read during this request. If
        this limit is exceeded, a 413 :exc:`~werkzeug.exceptions.RequestEntityTooLarge`
        error is raised. If it is set to ``None``, no limit is enforced at the
        Flask application level. However, if it is ``None`` and the request has
        no ``Content-Length`` header and the WSGI server does not indicate that
        it terminates the stream, then no data is read to avoid an infinite
        stream.

        Each request defaults to the :data:`MAX_CONTENT_LENGTH` config, which
        defaults to ``None``. It can be set on a specific ``request`` to apply
        the limit to that specific view. This should be set appropriately based
        on an application's or view's specific needs.

        .. versionchanged:: 3.1
            This can be set per-request.

        .. versionchanged:: 0.6
            This is configurable through Flask config.
        """
        if self._max_content_length is not None:
            return self._max_content_length

        if not current_app:
            return super().max_content_length

        return current_app.config["MAX_CONTENT_LENGTH"]  # type: ignore[no-any-return]

    @max_content_length.setter
    def max_content_length(self, value: int | None) -> None:
        self._max_content_length = value

    @property
    def max_form_memory_size(self) -> int | None:
        """The maximum size in bytes any non-file form field may be in a
        ``multipart/form-data`` body. If this limit is exceeded, a 413
        :exc:`~werkzeug.exceptions.RequestEntityTooLarge` error is raised. If it
        is set to ``None``, no limit is enforced at the Flask application level.

        Each request defaults to the :data:`MAX_FORM_MEMORY_SIZE` config, which
        defaults to ``500_000``. It can be set on a specific ``request`` to
        apply the limit to that specific view. This should be set appropriately
        based on an application's or view's specific needs.

        .. versionchanged:: 3.1
            This is configurable through Flask config.
        """
        if self._max_form_memory_size is not None:
            return self._max_form_memory_size

        if not current_app:
            return super().max_form_memory_size

        return current_app.config["MAX_FORM_MEMORY_SIZE"]  # type: ignore[no-any-return]

    @max_form_memory_size.setter
    def max_form_memory_size(self, value: int | None) -> None:
        self._max_form_memory_size = value

    @property  # type: ignore[override]
    def max_form_parts(self) -> int | None:
        """The maximum number of fields that may be present in a
        ``multipart/form-data`` body. If this limit is exceeded, a 413
        :exc:`~werkzeug.exceptions.RequestEntityTooLarge` error is raised. If it
        is set to ``None``, no limit is enforced at the Flask application level.

        Each request defaults to the :data:`MAX_FORM_PARTS` config, which
        defaults to ``1_000``. It can be set on a specific ``request`` to apply
        the limit to that specific view. This should be set appropriately based
        on an application's or view's specific needs.

        .. versionchanged:: 3.1
            This is configurable through Flask config.
        """
        if self._max_form_parts is not None:
            return self._max_form_parts

        if not current_app:
            return super().max_form_parts

        return current_app.config["MAX_FORM_PARTS"]  # type: ignore[no-any-return]

    @max_form_parts.setter
    def max_form_parts(self, value: int | None) -> None:
        self._max_form_parts = value

    @property
    def endpoint(self) -> str | None:
        """The endpoint that matched the request URL.

        This will be ``None`` if matching failed or has not been
        performed yet.

        This in combination with :attr:`view_args` can be used to
        reconstruct the same URL or a modified URL.
        """
        if self.url_rule is not None:
            return self.url_rule.endpoint  # type: ignore[no-any-return]

        return None

    @property
    def blueprint(self) -> str | None:
        """The registered name of the current blueprint.

        This will be ``None`` if the endpoint is not part of a
        blueprint, or if URL matching failed or has not been performed
        yet.

        This does not necessarily match the name the blueprint was
        created with. It may have been nested, or registered with a
        different name.
        """
        endpoint = self.endpoint

        if endpoint is not None and "." in endpoint:
            return endpoint.rpartition(".")[0]

        return None

    @property
    def blueprints(self) -> list[str]:
        """The registered names of the current blueprint upwards through
        parent blueprints.

        This will be an empty list if there is no current blueprint, or
        if URL matching failed.

        .. versionadded:: 2.0.1
        """
        name = self.blueprint

        if name is None:
            return []

        return _split_blueprint_path(name)

    def _load_form_data(self) -> None:
        if self._json_seq_iterating:
            raise RuntimeError(
                "Cannot parse form data because the request input stream is"
                " owned by a JSON sequence iterator created with"
                " 'iter_json_seq'."
            )

        super()._load_form_data()

        # In debug mode we're replacing the files multidict with an ad-hoc
        # subclass that raises a different error for key errors.
        if (
            current_app
            and current_app.debug
            and self.mimetype != "multipart/form-data"
            and not self.files
        ):
            from .debughelpers import attach_enctype_error_multidict

            attach_enctype_error_multidict(self)

    def on_json_loading_failed(self, e: ValueError | None) -> t.Any:
        try:
            return super().on_json_loading_failed(e)
        except BadRequest as ebr:
            if current_app and current_app.debug:
                raise

            raise BadRequest() from ebr

    @t.overload
    def get_data(
        self,
        cache: bool = True,
        as_text: t.Literal[False] = False,
        parse_form_data: bool = False,
    ) -> bytes: ...

    @t.overload
    def get_data(
        self,
        cache: bool = True,
        as_text: t.Literal[True] = ...,
        parse_form_data: bool = False,
    ) -> str: ...

    def get_data(
        self, cache: bool = True, as_text: bool = False, parse_form_data: bool = False
    ) -> bytes | str:
        if self._json_seq_iterating:
            raise RuntimeError(
                "Cannot read the request data because the input stream is"
                " owned by a JSON sequence iterator created with"
                " 'iter_json_seq'."
            )

        if as_text:
            return super().get_data(
                cache=cache, as_text=True, parse_form_data=parse_form_data
            )

        return super().get_data(
            cache=cache, as_text=False, parse_form_data=parse_form_data
        )

    @t.overload
    def get_json(
        self, force: bool = ..., silent: t.Literal[False] = ..., cache: bool = ...
    ) -> t.Any: ...

    @t.overload
    def get_json(
        self, force: bool = ..., silent: bool = ..., cache: bool = ...
    ) -> t.Any | None: ...

    def get_json(
        self, force: bool = False, silent: bool = False, cache: bool = True
    ) -> t.Any | None:
        if self._json_seq_iterating:
            raise RuntimeError(
                "Cannot load JSON data because the input stream is owned by"
                " a JSON sequence iterator created with 'iter_json_seq'."
            )

        return super().get_json(force=force, silent=silent, cache=cache)

    def iter_json_seq(
        self,
        *,
        on_error: t.Callable[[JsonSeqError], None] | None = None,
        max_record_bytes: int | None = None,
        chunk_size: int = 8192,
    ) -> t.Iterator[t.Any]:
        """Iterate over the request body as an RFC 7464 JSON text sequence,
        yielding one parsed value per record.

        This is intended for requests with the ``application/json-seq``
        mimetype, where the body is a stream of JSON texts delimited by the
        ASCII record separator (RS, 0x1E) control character. Unlike
        :meth:`get_json`, records are parsed and yielded incrementally as
        they arrive, so earlier records are available before the complete
        request body has been received. Iteration is lazy: if the consumer
        stops iterating, the remainder of the body is not read.

        Records are decoded as UTF-8 and parsed with the app's JSON provider.
        Multi-byte characters, separators, and JSON tokens may be split
        across read chunks at any point.

        A record that cannot be parsed is handled according to the mode:

        -   Strict mode (the default, ``on_error`` is not given):
            iteration raises :class:`~flask.json_seq.JsonSeqError` at the
            first bad record.
        -   Skip mode (``on_error`` is given): the callback is invoked
            with a :class:`~flask.json_seq.JsonSeqError` carrying the bad
            record's sequence ``index``, its ``start`` and
            ``end`` byte offsets in the request body, and an error
            ``category``. Iteration then resumes at the next record
            separator.

        The error category is one of:

        -   ``"invalid"``: the record is not valid UTF-8 or not valid
            JSON.
        -   ``"truncated"``: the record is missing its line feed
            terminator and does not parse, indicating it was cut off.
            Following RFC 7464, truncation of top-level numbers and the
            literals ``true``, ``false``, and ``null`` is not
            detectable, so such records are yielded as-is.
        -   ``"too_large"``: the record exceeded
            ``max_record_bytes``.

        :param on_error: Callback invoked with a
            :class:`~flask.json_seq.JsonSeqError` for each bad record. If
            not given, the first bad record raises instead.
        :param max_record_bytes: Maximum size of a single record in bytes.
            Larger records are reported as ``"too_large"`` and discarded
            while scanning for the next separator, without buffering their
            contents, keeping memory bounded.
        :param chunk_size: Number of bytes to read from the input stream at
            a time.

        The iterator takes exclusive ownership of the request's input stream.
        It cannot be combined with :meth:`get_data`, :meth:`get_json`,
        form parsing, or a second iterator. Conflicting uses raise
        :exc:`RuntimeError` rather than silently returning partial data.

        .. versionadded:: 3.2
        """
        if self._json_seq_iterating:
            raise RuntimeError(
                "A JSON sequence iterator was already created for this"
                " request. The input stream can only be consumed once."
            )

        if self._json_seq_stream_consumed():
            raise RuntimeError(
                "Cannot iterate the request as a JSON sequence because the"
                " input stream was already consumed, for example by"
                " 'get_data', 'get_json', or form parsing."
            )

        self._json_seq_iterating = True

        return _iter_json_seq(
            self.stream,
            self.json_module.loads,
            on_error=on_error,
            max_record_bytes=max_record_bytes,
            chunk_size=chunk_size,
        )

    def _json_seq_stream_consumed(self) -> bool:
        """Check whether the request input stream was already consumed, at
        least in part, by another reader such as :meth:`get_data` or form
        parsing.

        .. versionadded:: 3.2
        """
        if getattr(self, "_cached_data", None) is not None:
            return True

        if "form" in self.__dict__ and self.want_form_data_parsed:
            return True

        stream = self.__dict__.get("stream")

        if stream is not None:
            tell = getattr(stream, "tell", None)

            if tell is not None:
                try:
                    if tell():
                        return True
                except OSError:
                    pass

        return False


class Response(ResponseBase):
    """The response object that is used by default in Flask.  Works like the
    response object from Werkzeug but is set to have an HTML mimetype by
    default.  Quite often you don't have to create this object yourself because
    :meth:`~flask.Flask.make_response` will take care of that for you.

    If you want to replace the response object used you can subclass this and
    set :attr:`~flask.Flask.response_class` to your subclass.

    .. versionchanged:: 1.0
        JSON support is added to the response, like the request. This is useful
        when testing to get the test client response data as JSON.

    .. versionchanged:: 1.0

        Added :attr:`max_cookie_size`.
    """

    default_mimetype: str | None = "text/html"

    json_module = json

    autocorrect_location_header = False

    @property
    def max_cookie_size(self) -> int:  # type: ignore
        """Read-only view of the :data:`MAX_COOKIE_SIZE` config key.

        See :attr:`~werkzeug.wrappers.Response.max_cookie_size` in
        Werkzeug's docs.
        """
        if current_app:
            return current_app.config["MAX_COOKIE_SIZE"]  # type: ignore[no-any-return]

        # return Werkzeug's default when not in an app context
        return super().max_cookie_size
