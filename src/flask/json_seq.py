from __future__ import annotations

import typing as t

#: The ASCII "record separator" control character (0x1E) that delimits
#: records in an RFC 7464 JSON text sequence.
_RS = b"\x1e"

#: Error category for a record that is not valid UTF-8 or not valid JSON.
INVALID = "invalid"

#: Error category for a record that is missing its line feed terminator and
#: does not parse, indicating it was cut off.
TRUNCATED = "truncated"

#: Error category for a record that exceeded the configured byte limit.
TOO_LARGE = "too_large"


class JsonSeqError(ValueError):
    """Describes a record in an RFC 7464 JSON text sequence that could not be
    parsed.

    In strict mode this is raised by the iterator. In skip mode it is passed
    to the ``on_error`` callback instead.

    .. versionadded:: 3.2
    """

    def __init__(self, index: int, start: int, end: int, category: str) -> None:
        #: The position of the record in the sequence, counting from 0. Empty
        #: records (such as from consecutive separators) are not counted.
        self.index = index

        #: The byte offset of the first byte of the record in the request
        #: body, after its leading record separator.
        self.start = start

        #: The byte offset one past the last byte of the record. This is the
        #: position of the next record separator, or the end of the body.
        self.end = end

        #: The kind of error: ``"invalid"``, ``"truncated"``, or
        #: ``"too_large"``.
        self.category = category

        super().__init__(
            f"JSON sequence record {index} at bytes {start}:{end} is {category}"
        )


def iter_json_seq(
    stream: t.IO[bytes],
    loads: t.Callable[[str], t.Any],
    *,
    on_error: t.Callable[[JsonSeqError], None] | None = None,
    max_record_bytes: int | None = None,
    chunk_size: int = 8192,
) -> t.Iterator[t.Any]:
    """Incrementally parse an RFC 7464 JSON text sequence from a binary
    stream, yielding one parsed value per record.

    Records are delimited by the ASCII record separator (0x1E) and are
    decoded as UTF-8. Values are yielded as soon as their record is complete,
    before the rest of the stream has been read. Iteration is lazy: if the
    consumer stops, no more of the stream is read.

    See :meth:`flask.Request.iter_json_seq` for the request-level interface
    and a description of the error categories.

    :param stream: The binary stream to read from.
    :param loads: Callable used to parse each record's text, such as
        :func:`flask.json.loads`.
    :param on_error: If given, bad records are reported to this callback with
        a :class:`JsonSeqError` and iteration resumes at the next record.
        If not given, the first bad record raises :class:`JsonSeqError`.
    :param max_record_bytes: Maximum size of a single record in bytes. Larger
        records are reported as ``"too_large"`` and discarded without
        buffering their contents, keeping memory bounded.
    :param chunk_size: Number of bytes to read from the stream at a time.

    .. versionadded:: 3.2
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")

    if max_record_bytes is not None and max_record_bytes < 1:
        raise ValueError("max_record_bytes must be at least 1")

    buf = bytearray()
    out: list[t.Any] = []
    offset = 0
    record_start = 0
    index = 0
    oversized = False

    def report(category: str, rec_index: int, start: int, end: int) -> None:
        error = JsonSeqError(rec_index, start, end, category)

        if on_error is None:
            raise error

        on_error(error)

    def finish_record(data: bytes, start: int, end: int) -> None:
        nonlocal index

        terminated = data.endswith(b"\n")
        body = data[:-1] if terminated else data

        if not body.strip():
            # Empty records, such as from consecutive separators, are ignored.
            return

        rec_index = index
        index += 1

        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            report(INVALID, rec_index, start, end)
            return

        try:
            value = loads(text)
        except ValueError:
            # A record without its line feed terminator that does not parse
            # was cut off. Top-level numbers and the literals true, false,
            # and null parse as shorter valid values when truncated, so per
            # RFC 7464 they cannot be detected and are yielded as-is.
            report(INVALID if terminated else TRUNCATED, rec_index, start, end)
            return

        out.append(value)

    def close_segment(end: int) -> None:
        nonlocal index, oversized

        if oversized:
            oversized = False
            rec_index = index
            index += 1
            report(TOO_LARGE, rec_index, record_start, end)
        else:
            finish_record(bytes(buf), record_start, end)

        buf.clear()

    while True:
        chunk = stream.read(chunk_size)

        if not chunk:
            close_segment(offset)
            break

        pos = 0

        while pos < len(chunk):
            rs_at = chunk.find(_RS, pos)

            if rs_at < 0:
                piece = chunk[pos:]
            else:
                piece = chunk[pos:rs_at]

            if piece and not oversized:
                if (
                    max_record_bytes is not None
                    and len(buf) + len(piece) > max_record_bytes
                ):
                    # Stop buffering and discard the rest of this record as it
                    # arrives, keeping memory bounded for oversized records.
                    oversized = True
                    buf.clear()
                else:
                    buf += piece

            offset += len(piece)

            if rs_at < 0:
                break

            close_segment(offset)
            offset += 1
            record_start = offset
            pos = rs_at + 1

        if out:
            yield from out
            out.clear()

    yield from out
