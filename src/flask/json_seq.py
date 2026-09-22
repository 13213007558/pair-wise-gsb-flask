"""Streaming support for ``application/json-seq`` request bodies."""

from __future__ import annotations

import codecs
import re
import typing as t

from werkzeug.exceptions import BadRequest

RS = 0x1E

ENCODING_ERROR = "encoding"
SYNTAX_ERROR = "syntax"
TOO_LARGE_ERROR = "too_large"
TRUNCATED_ERROR = "truncated"

ErrorCategory = str
ErrorCallback = t.Callable[[int, tuple[int, int], ErrorCategory], t.Any]


class JSONSequenceError(BadRequest):
    """Raised when a JSON text sequence record cannot be parsed."""

    def __init__(
        self,
        category: ErrorCategory,
        index: int,
        start: int,
        end: int,
    ) -> None:
        super().__init__(
            f"JSON sequence record {index} at bytes {start}:{end} is invalid"
            f" ({category})."
        )
        self.category = category
        self.index = index
        self.byte_range = (start, end)


_PRIMITIVE_PREFIX = re.compile(
    r"\s*-?(?:0|[1-9][0-9]*)?(?:\.[0-9]*)?(?:[eE][+-]?[0-9]*)?\s*$"
)


def _string_is_truncated(text: str) -> bool:
    stripped = text.strip()
    escaped = False
    unicode_digits = 0
    for index, char in enumerate(stripped):
        if index == 0:
            continue
        if unicode_digits:
            if char in "0123456789abcdefABCDEF":
                unicode_digits -= 1
                continue
            return False
        if escaped:
            escaped = False
            if char == "u":
                unicode_digits = 4
        elif char == "\\":
            escaped = True
        elif char == '"':
            return False
    return True


def _is_truncated(text: str, error: ValueError) -> bool:
    stripped = text.strip()

    if not stripped:
        return True

    for literal in ("true", "false", "null"):
        if literal.startswith(stripped) and stripped[0] in "tfn":
            return True

    if stripped[0] in "-0123456789" and _PRIMITIVE_PREFIX.fullmatch(text):
        return True

    if stripped.startswith('"') and _string_is_truncated(text):
        return True

    if stripped[0] == "[":
        inner = stripped[1:].strip()
        return (
            not inner
            or _is_truncated_value(inner, error)
            or getattr(error, "pos", 0) >= len(text)
        )

    if stripped[0] == "{":
        inner = stripped[1:].strip()
        if not inner or inner.endswith(("{", "[", ",", ":", '"')):
            return True
        return getattr(error, "pos", 0) >= len(text)

    return False


def _is_truncated_value(text: str, error: ValueError) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    for literal in ("true", "false", "null"):
        if literal.startswith(stripped) and stripped[0] in "tfn":
            return True
    if stripped[0] in "-0123456789" and _PRIMITIVE_PREFIX.fullmatch(text):
        return True
    if stripped.startswith('"') and _string_is_truncated(text):
        return True
    if stripped[0] in "[{":
        return getattr(error, "pos", 0) >= len(text)
    return False


class _JSONSeqIterator(t.Iterator[t.Any]):
    def __init__(
        self,
        request: t.Any,
        *,
        strict: bool,
        on_bad_record: ErrorCallback | None,
        max_record_size: int | None,
    ) -> None:
        if not strict and not callable(on_bad_record):
            raise TypeError("on_bad_record is required when strict is False")
        if max_record_size is not None and max_record_size < 0:
            raise ValueError("max_record_size must be non-negative or None")

        self._json_module = request.json_module
        self._stream = request.stream
        self._strict = strict
        self._on_bad_record = on_bad_record
        self._max_record_size = max_record_size
        self._position = 0
        self._index = -1
        self._phase = "seek_rs"
        self._finished = False
        self._record_start = 0
        self._buffer = bytearray()
        self._decoder: codecs.IncrementalDecoder | None = None
        self._depth = 0
        self._in_string = False
        self._escaped = False
        self._discarding = False
        self._error_category: ErrorCategory | None = None
        self._candidate_value: t.Any | None = None
        self._has_candidate = False

    def __iter__(self) -> t.Self:
        return self

    def _read_byte(self) -> int | None:
        chunk = self._stream.read(1)

        if not chunk:
            return None

        self._position += 1
        return chunk[0]

    def _fail(self, category: ErrorCategory, start: int, end: int) -> None:
        error = JSONSequenceError(category, self._index, start, end)

        if self._strict:
            raise error

        assert self._on_bad_record is not None
        self._on_bad_record(self._index, (start, end), category)

    def _reset_record(self) -> None:
        self._record_start = self._position
        self._buffer = bytearray()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self._depth = 0
        self._in_string = False
        self._escaped = False
        self._discarding = False
        self._error_category = None
        self._candidate_value = None
        self._has_candidate = False

    def _start_next_record(self) -> None:
        self._index += 1
        self._reset_record()
        self._phase = "record"

    def _update_frame(self, byte: int) -> None:
        if self._in_string:
            if self._escaped:
                self._escaped = False
            elif byte == 0x5C:
                self._escaped = True
            elif byte == 0x22:
                self._in_string = False
            return

        if byte == 0x22:
            self._in_string = True
        elif byte in (0x7B, 0x5B):
            self._depth += 1
        elif byte in (0x7D, 0x5D) and self._depth:
            self._depth -= 1

    def _text(self, final: bool = False) -> str:
        return bytes(self._buffer).decode("utf-8")

    def _classify_failure(self, error: ValueError) -> ErrorCategory:
        if isinstance(error, UnicodeDecodeError):
            return ENCODING_ERROR

        try:
            text = self._text(final=False)
        except UnicodeDecodeError:
            return ENCODING_ERROR

        return TRUNCATED_ERROR if _is_truncated(text, error) else SYNTAX_ERROR

    def _discard_to_rs(self) -> None:
        while True:
            byte = self._read_byte()

            if byte is None:
                self._fail(
                    self._error_category or SYNTAX_ERROR,
                    self._record_start,
                    self._position,
                )
                self._phase = "finished"
                return

            if byte == RS:
                self._fail(
                    self._error_category or SYNTAX_ERROR,
                    self._record_start,
                    self._position - 1,
                )
                self._start_next_record()
                return

    def _finish_bad_boundary(self, end: int, closed: bool) -> None:
        try:
            self._json_module.loads(self._text(final=True))
        except ValueError as error:
            self._error_category = self._classify_failure(error)
        else:
            self._error_category = TRUNCATED_ERROR

        if closed:
            self._fail(self._error_category or SYNTAX_ERROR, self._record_start, end)
            self._start_next_record()
        else:
            self._fail(self._error_category or SYNTAX_ERROR, self._record_start, end)
            self._phase = "finished"

    def _get_candidate(self) -> bool:
        if self._in_string or self._depth:
            return False

        try:
            value = self._json_module.loads(self._text(final=False))
        except ValueError:
            return False

        self._candidate_value = value
        self._has_candidate = True
        return True

    def _set_candidate(self, value: t.Any) -> None:
        self._candidate_value = value
        self._has_candidate = True

    def _candidate_kind(self) -> str:
        if self._candidate_value is None or isinstance(
            self._candidate_value, (int, float, bool)
        ):
            return "primitive"
        return "self_delimited"

    def _yield_candidate(self) -> t.Any:
        value = self._candidate_value
        self._candidate_value = None
        self._has_candidate = False
        self._phase = "record_complete"
        return value

    def __next__(self) -> t.Any:
        if self._finished:
            raise StopIteration

        while True:
            byte = self._read_byte()

            if self._phase == "seek_rs":
                if byte is None:
                    self._finished = True
                    raise StopIteration
                if byte == RS:
                    self._start_next_record()
                continue

            if self._phase == "record_complete":
                if byte is None:
                    self._finished = True
                    raise StopIteration
                if byte == RS:
                    self._start_next_record()
                elif byte == 0x0A:
                    self._phase = "after_lf"
                continue

            if self._phase == "after_lf":
                if byte is None:
                    self._finished = True
                    raise StopIteration
                if byte == RS:
                    self._start_next_record()
                else:
                    self._error_category = SYNTAX_ERROR
                    self._discard_to_rs()
                continue

            if byte is None:
                if self._discarding:
                    self._fail(
                        self._error_category or SYNTAX_ERROR,
                        self._record_start,
                        self._position,
                    )
                elif self._has_candidate:
                    return self._yield_candidate()
                elif self._buffer:
                    self._finish_bad_boundary(self._position, closed=False)
                self._finished = True
                raise StopIteration

            if byte == RS:
                if self._discarding:
                    self._fail(
                        self._error_category or SYNTAX_ERROR,
                        self._record_start,
                        self._position - 1,
                    )
                    self._start_next_record()
                elif self._has_candidate:
                    if self._candidate_kind() == "self_delimited":
                        value = self._yield_candidate()
                        self._start_next_record()
                        return value
                    self._finish_bad_boundary(self._position - 1, closed=True)
                elif self._buffer:
                    self._finish_bad_boundary(self._position - 1, closed=True)
                else:
                    # Consecutive RS markers delimit an empty, invalid record.
                    self._error_category = SYNTAX_ERROR
                    self._fail(SYNTAX_ERROR, self._record_start, self._position - 1)
                    self._start_next_record()
                continue

            if self._discarding:
                continue

            if byte == 0x0A and self._depth == 0:
                if self._in_string:
                    pass
                elif not self._buffer.strip():
                    self._error_category = SYNTAX_ERROR
                    self._discard_to_rs()
                    continue
                else:
                    if self._has_candidate:
                        continue

                    try:
                        candidate = self._json_module.loads(self._text(final=True))
                    except ValueError as error:
                        self._error_category = self._classify_failure(error)
                        self._discard_to_rs()
                        continue

                    self._set_candidate(candidate)
                    if self._candidate_kind() == "primitive":
                        return self._yield_candidate()
                    continue

            try:
                assert self._decoder is not None
                self._decoder.decode(bytes((byte,)), final=False)
            except UnicodeDecodeError:
                self._discarding = True
                self._error_category = ENCODING_ERROR
                continue

            old_depth = self._depth
            old_in_string = self._in_string
            self._buffer.append(byte)
            self._update_frame(byte)

            if (
                self._max_record_size is not None
                and len(self._buffer) > self._max_record_size
            ):
                self._discarding = True
                self._error_category = TOO_LARGE_ERROR
                continue

            if (
                (old_depth and not self._depth)
                or (old_in_string and not self._in_string)
            ):
                self._get_candidate()
                if self._has_candidate and isinstance(
                    self._candidate_value, (dict, list, str)
                ):
                    return self._yield_candidate()
