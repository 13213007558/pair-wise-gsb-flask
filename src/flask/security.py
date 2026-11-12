from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:  # pragma: no cover
    from .wrappers import Response


class ResponsePolicyContext:
    """Information passed to each :meth:`.Scaffold.response_policy`
    function. Policies mutate the :attr:`response` in place, usually by
    setting security headers such as ``Content-Security-Policy``.

    The same context object is passed to every policy for a request. The
    policy runs once, after all ``after_request`` functions and after the
    session has been saved, so a header set by a policy overrides the same
    header set earlier in the request.

    :param response: The finalized response, before it is sent.
    :param endpoint: The matched endpoint, or ``None`` for a routing error
        such as 404 or 405.
    :param error: The exception being handled, or ``None`` for a normal
        response. This is the original exception, not the
        :class:`~werkzeug.exceptions.InternalServerError` wrapper that may
        have been used to build the response.

    .. versionadded:: 3.2
    """

    def __init__(
        self,
        response: Response,
        endpoint: str | None,
        error: BaseException | None,
    ) -> None:
        self.response = response
        self.endpoint = endpoint
        self.error = error

    @property
    def status_code(self) -> int:
        """The HTTP status code of the :attr:`response`."""
        return self.response.status_code  # type: ignore[no-any-return]

    def __repr__(self) -> str:
        return (
            f"<ResponsePolicyContext {self.status_code}"
            f" endpoint={self.endpoint!r} error={self.error!r}>"
        )


class _WSGICommitState:
    """Tracks whether ``start_response`` has committed the headers."""

    def __init__(self) -> None:
        self.headers_sent = False


class _PolicyResponseIterator:
    """Wrap the response iterable while response policies are enabled.

    Response policies can only change headers before the WSGI server calls
    ``start_response``. If a streaming response raises while its body is
    being iterated, the headers are already committed and no policy (or any
    other code) can add or replace a header at that point. This wrapper
    records that boundary and reports it instead of pretending the response
    can still be modified. The exception is always re-raised and the wrapped
    iterable's ``close`` method is delegated to, so resources are cleaned up
    even if the stream is closed early.
    """

    def __init__(
        self,
        app: t.Any,
        iterable: t.Iterable[bytes],
        commit_state: _WSGICommitState,
    ) -> None:
        self._app = app
        self._iterator = iter(iterable)
        self._close = getattr(iterable, "close", None)
        self._commit_state = commit_state

    def __iter__(self) -> _PolicyResponseIterator:
        return self

    def __next__(self) -> bytes:
        try:
            return next(self._iterator)
        except StopIteration:
            # Part of the iterator protocol, never treat the end of the
            # stream as a failure.
            raise
        except Exception as error:
            if self._commit_state.headers_sent:
                self._app.logger.error(
                    "Response stream failed after headers were committed;"
                    " response policies cannot add or change headers for"
                    " this response.",
                    exc_info=error,
                )

            raise

    def close(self) -> None:
        if self._close is not None:
            self._close()
