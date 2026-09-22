from __future__ import annotations

import typing as t

from werkzeug.exceptions import BadRequest
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Request as RequestBase
from werkzeug.wrappers import Response as ResponseBase

from . import json
from .globals import current_app
from .helpers import _split_blueprint_path
from .json_seq import ErrorCallback
from .json_seq import _JSONSeqIterator

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
    _body_consumed: bool = False
    _json_seq_iterator: _JSONSeqIterator | None = None

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
        if self._json_seq_iterator is not None:
            self._assert_no_body_conflict()

        super()._load_form_data()

        if self.form or self.files:
            self._body_consumed = True

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

    @property
    def stream(self):  # type: ignore[override]
        if self._json_seq_iterator is not None:
            return self._json_seq_iterator._stream

        return _ConsumingStream(self, super().stream)

    @property
    def is_json_seq(self) -> bool:
        return self.mimetype == "application/json-seq"

    def _assert_no_body_conflict(self) -> None:
        if self._json_seq_iterator is not None:
            raise RuntimeError(
                "The request body is being consumed by a JSON sequence"
                " iterator."
            )

    def _assert_json_seq_available(self) -> None:
        if self._json_seq_iterator is not None:
            raise RuntimeError(
                "A JSON sequence iterator is already consuming this request body."
            )

        if getattr(self, "_cached_data", None) is not None or self._body_consumed:
            raise RuntimeError("The request body has already been consumed.")

    def get_data(
        self, cache: bool = True, as_text: bool = False, parse_form_data: bool = False
    ) -> bytes | str:
        self._assert_no_body_conflict()

        if self._body_consumed and getattr(self, "_cached_data", None) is None:
            raise RuntimeError("The request body has already been consumed.")

        if not cache:
            self._body_consumed = True

        return super().get_data(
            cache=cache, as_text=as_text, parse_form_data=parse_form_data
        )

    def get_json(
        self, force: bool = False, silent: bool = False, cache: bool = True
    ) -> t.Any | None:
        self._assert_no_body_conflict()

        if self._body_consumed and getattr(self, "_cached_data", None) is None:
            raise RuntimeError("The request body has already been consumed.")

        return super().get_json(force=force, silent=silent, cache=cache)

    def iter_json_seq(
        self,
        *,
        force: bool = False,
        strict: bool = True,
        on_bad_record: ErrorCallback | None = None,
        max_record_size: int | None = 1024 * 1024,
    ) -> t.Iterator[t.Any]:
        """Yield records from an RFC 7464 ``application/json-seq`` body.

        Records are read from the uncached request stream. In non-strict mode,
        ``on_bad_record`` receives the zero-based record index, a half-open
        byte range, and the error category, and reading resumes at the next
        RS byte.
        """
        if not (force or self.is_json_seq):
            return self.on_json_loading_failed(None)

        self._assert_json_seq_available()
        iterator = _JSONSeqIterator(
            self,
            strict=strict,
            on_bad_record=on_bad_record,
            max_record_size=max_record_size,
        )
        self._json_seq_iterator = iterator
        self._body_consumed = True
        return iterator

    iter_json_sequence = iter_json_seq

    def on_json_loading_failed(self, e: ValueError | None) -> t.Any:
        try:
            return super().on_json_loading_failed(e)
        except BadRequest as ebr:
            if current_app and current_app.debug:
                raise

            raise BadRequest() from ebr


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


class _ConsumingStream:
    def __init__(self, request: Request, stream: t.IO[bytes]) -> None:
        self.__dict__["_request"] = request
        self.__dict__["_stream"] = stream

    def _consume(self, data: bytes) -> bytes:
        if data:
            self._request._body_consumed = True
        return data

    def read(self, size: int = -1) -> bytes:  # type: ignore[override]
        return self._consume(self._stream.read(size))

    def readline(self, size: int = -1) -> bytes:  # type: ignore[override]
        return self._consume(self._stream.readline(size))

    def readlines(self, hint: int = -1) -> list[bytes]:
        lines = self._stream.readlines(hint)
        if lines:
            self._request._body_consumed = True
        return lines

    def __iter__(self):
        for line in self._stream:
            if line:
                self._request._body_consumed = True
            yield line

    def read1(self, size: int = -1) -> bytes:  # type: ignore[override]
        return self._consume(self._stream.read1(size))

    def readinto(self, buffer: bytearray) -> int:  # type: ignore[override]
        size = self._stream.readinto(buffer)
        if size:
            self._request._body_consumed = True
        return size

    def __getattr__(self, name: str) -> t.Any:
        return getattr(self._stream, name)
