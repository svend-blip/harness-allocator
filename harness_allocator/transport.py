"""Atomic request transport and payload identity for the Harness Terminal.

The terminal's stdin is a byte stream. A naive ``readline`` loop lets embedded
newlines fragment one semantic task into many harness turns — a real, live bug
reproduced multiple times. This module defines an optional length-delimited
frame so that, when the dispatcher chooses to use one, ONE complete semantic
task maps to EXACTLY ONE frame, regardless of how many newlines the payload
contains.

Frame layout (UTF-8)::

    HAR-FRAME <request_id> <byte_length> [retry]\n
    <exactly ``byte_length`` bytes of payload>

The header is a single line; the payload follows verbatim and its newlines are
data, never a request boundary. ``byte_length`` counts UTF-8 bytes, so the
decoder reads the exact number of bytes without depending on character width.

The optional ``retry`` token is the ONLY way to re-execute a completed request
identity: a frame carrying it bypasses duplicate-request protection and is run
again. Its absence means "do not run this identity twice".

A transitional legacy path is also supported: a bare single line (no header) is
accepted as a one-line request with a synthesized ``request_id``. By construction
such a request is a single line and therefore cannot be fragmented; the framed
protocol is authoritative for anything multi-line.

This module is implementation detail, not a binding DPMtF transport contract.
Callers are free to feed :func:`~harness_allocator.invoke.execute` directly
with a complete Python string — that is the canonical, no-protocol-required
path. The framing is provided for callers that prefer to stream stdin into
the persistent terminal.
"""

from __future__ import annotations

import hashlib
import itertools
import re
from dataclasses import dataclass

#: The frame header magic word. A header line is ``HAR-FRAME <id> <len> [retry]``.
HEADER_MAGIC = "HAR-FRAME"
_HEADER_MAGIC_BYTES = HEADER_MAGIC.encode("ascii")

#: Request ids must be a single token: alphanumeric plus ``. _ -``.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: Read chunk size for buffered stream reads.
_CHUNK = 65536

#: Monotonic counter backing :func:`make_request_id`.
_id_counter = itertools.count(1)


class TransportError(ValueError):
    """Raised when a frame header or payload is malformed or undecodable."""


@dataclass(frozen=True)
class RequestFrame:
    """One complete, atomic semantic request.

    ``retry`` is ``True`` only when the frame's header carries the explicit
    ``retry`` token — the sole signal that re-executes a completed identity.
    """

    request_id: str
    payload: str
    retry: bool = False


@dataclass(frozen=True)
class PayloadIdentity:
    """Operational identity for a request payload — never chain-of-thought."""

    request_id: str
    chars: int
    lines: int
    sha256: str

    def as_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "chars": self.chars,
            "lines": self.lines,
            "sha256": self.sha256,
        }


def make_request_id(prefix: str = "ha") -> str:
    """A fresh, process-unique request id (used for legacy/synthesized ids)."""
    return f"{prefix}-{next(_id_counter):06d}"


def compute_identity(request_id: str, payload: str) -> PayloadIdentity:
    """Stable operational metadata for a payload: chars, lines, sha256.

    ``chars`` is the Python character count; ``lines`` is
    ``len(payload.splitlines())``; ``sha256`` is the hex digest of the UTF-8
    payload. These are execution metadata for truncation/fragmentation
    detection — never model chain-of-thought.
    """
    return PayloadIdentity(
        request_id=request_id,
        chars=len(payload),
        lines=len(payload.splitlines()),
        sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )


def _validate_request_id(request_id: str) -> None:
    if not request_id or not _REQUEST_ID_RE.match(request_id):
        raise TransportError(f"invalid request_id: {request_id!r}")


def encode_request(request_id: str, payload: str, retry: bool = False) -> bytes:
    """Encode one request as a single length-delimited frame.

    Returns the header line plus the exact UTF-8 payload bytes. The caller
    writes these bytes verbatim (atomically) to the terminal's stdin. ``retry``
    adds the explicit ``retry`` token to the header, which re-executes a
    completed request identity instead of being reported as a duplicate.
    """
    _validate_request_id(request_id)
    data = payload.encode("utf-8")
    flag = " retry" if retry else ""
    header = f"{HEADER_MAGIC} {request_id} {len(data)}{flag}\n".encode("ascii")
    return header + data


def extract_frame(data: bytes) -> tuple[RequestFrame | None, bytes]:
    """Parse at most one complete frame from ``data``.

    Returns ``(frame, rest)``. ``frame`` is ``None`` when ``data`` holds an
    incomplete header or payload (``rest`` is then the full buffer to keep
    buffering). Raises :class:`TransportError` on a malformed header, an
    invalid request id, a non-integer/negative length, an unknown frame flag,
    or an undecodable payload.
    """
    nl = data.find(b"\n")
    if nl < 0:
        return None, data
    header_line = data[:nl].decode("ascii", "replace")
    if not header_line.startswith(HEADER_MAGIC + " "):
        raise TransportError(f"unexpected header line: {header_line[:80]!r}")
    parts = header_line.split()
    if len(parts) not in (3, 4):
        raise TransportError(f"malformed frame header: {header_line[:80]!r}")
    request_id = parts[1]
    _validate_request_id(request_id)
    retry = False
    if len(parts) == 4:
        if parts[3] != "retry":
            raise TransportError(f"unknown frame flag: {parts[3]!r}")
        retry = True
    try:
        length = int(parts[2])
    except ValueError as exc:
        raise TransportError(f"non-integer byte length: {parts[2]!r}") from exc
    if length < 0:
        raise TransportError(f"negative byte length: {length}")
    payload_start = nl + 1
    if len(data) < payload_start + length:
        return None, data
    payload_bytes = data[payload_start : payload_start + length]
    try:
        payload = payload_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TransportError(f"payload is not valid UTF-8: {exc}") from exc
    return (
        RequestFrame(request_id=request_id, payload=payload, retry=retry),
        data[payload_start + length :],
    )


def _read_more(stream, size):
    """Read up to ``size`` bytes without requiring a full ``size``-byte fill.

    Prefers ``read1`` (available on buffered streams such as ``sys.stdin.buffer``)
    so an interactive terminal returns as soon as bytes are available; falls back
    to ``read`` for in-memory streams used in tests.
    """
    read1 = getattr(stream, "read1", None)
    if read1 is not None:
        return read1(size)
    return stream.read(size)


class FrameReader:
    """Incrementally read framed requests (plus legacy single lines) from a stream.

    ``stream`` is any object with ``read(n)`` returning ``b""`` at EOF (or
    ``read1(n)`` when available), such as ``sys.stdin.buffer``.
    """

    def __init__(self, stream):
        self._stream = stream
        self._buf = b""

    def read_frame(self) -> RequestFrame | None:
        """Return the next complete non-blank request, or ``None`` at EOF.

        Blank legacy lines (a stray Enter) are skipped internally and never
        surface as a request. Raises :class:`TransportError` on a malformed
        framed header.
        """
        while True:
            if self._buf.startswith(_HEADER_MAGIC_BYTES):
                frame, rest = extract_frame(self._buf)
                if frame is not None:
                    self._buf = rest
                    return frame
                # Incomplete frame: fall through and buffer more bytes.
            else:
                nl = self._buf.find(b"\n")
                if nl >= 0:
                    line = self._buf[:nl]
                    self._buf = self._buf[nl + 1 :]
                    payload = line.decode("utf-8")
                    if payload.strip():
                        return RequestFrame(request_id=make_request_id(), payload=payload)
                    continue
            chunk = _read_more(self._stream, _CHUNK)
            if not chunk:
                return None
            self._buf += chunk
