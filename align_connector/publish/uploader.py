# -*- coding: utf-8 -*-
"""Sending a large file, and surviving the transfer (TCK-1664).

A multi-gigabyte raster takes hours on an ordinary connection. Over hours, a
dropped Wi-Fi, a VPN reconnecting or a laptop going to sleep is not a risk — it
is a schedule. So this module never assumes the transfer completes in one go:

* the file is read **in chunks** and never loaded into memory (an 8 MiB buffer,
  whatever the file weighs);
* after an interruption the uploader **asks the store how far it got** and
  resumes from there, instead of starting over at 90 %;
* progress and cancellation are reported between chunks, so three hours of work
  never look like a frozen application.

The wire protocol is the store's, not ours: ALIGN names it when it opens the
session (``protocol``), because a resumable upload speaks the dialect of
whichever object store is behind the platform — and that store is changing.
Today only ``gcs-resumable`` exists; the transport is a separate object so
adding the S3 dialect is adding a class, not rewriting this loop.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import time

#: Sent to ask the store what it already holds, without sending any payload.
_PROBE = object()

#: How many times a chunk is retried before the upload gives up. Each retry
#: re-asks the committed offset first, so a retry never duplicates bytes.
MAX_CHUNK_ATTEMPTS = 4

#: Base of the exponential backoff between retries, in seconds.
RETRY_BACKOFF_SEC = 2


class UploadCancelled(Exception):
    """The user asked to stop. Not an error — a decision."""


class UploadFailed(Exception):
    """The transfer could not complete, with a reason fit to show a human."""


class ChunkResult:
    """What the store answered to one chunk.

    :param committed: bytes the store confirms it holds (``None`` when unknown)
    :param complete: whether the object is now fully written
    :param error: a human-readable reason, when the chunk was refused outright
    """

    def __init__(self, committed=None, complete=False, error=None):
        self.committed = committed
        self.complete = complete
        self.error = error


class GcsResumableTransport:
    """The ``gcs-resumable`` dialect.

    Chunks are PUT to the session URL with a ``Content-Range`` header. The store
    answers ``308`` while it wants more, and ``200``/``201`` when the object is
    complete. Asking where we stand is the same request with no body and a
    ``bytes*/total`` range — which is why resuming needs no bookkeeping of our
    own: the store is the authority on what it holds.
    """

    def __init__(self, session_url):
        self.session_url = session_url

    def send(self, data, start, total):
        # Imported here rather than at module scope so the uploader's logic can
        # be exercised without QGIS present.
        from .. import net

        if data is _PROBE:
            headers = {"Content-Range": "bytes */{}".format(total)}
            payload = b""
        else:
            end = start + len(data) - 1
            headers = {"Content-Range": "bytes {}-{}/{}".format(start, end, total)}
            payload = data

        result = net.put_bytes(self.session_url, payload, headers=headers, manual_redirects=True)

        if result.status in (200, 201):
            return ChunkResult(committed=total, complete=True)
        if result.status == 308:
            # `Range: bytes=0-<last>` — absent means the store holds nothing yet.
            return ChunkResult(committed=_parse_committed(result.headers))
        if result.status in (404, 410):
            return ChunkResult(error="The upload session expired. Start the publish again.")
        if result.status == 403:
            return ChunkResult(error="The upload session is no longer authorised.")
        return ChunkResult()  # transient: no verdict, worth retrying


def _parse_committed(headers):
    """Bytes the store confirms, read from its ``Range`` answer."""
    raw = (headers or {}).get("range") or (headers or {}).get("Range")
    if not raw or "-" not in raw:
        return 0
    try:
        return int(raw.rsplit("-", 1)[1]) + 1
    except (ValueError, IndexError):
        return 0


class ResumableUploader:
    """Drives one file to one session, from wherever the store left off."""

    def __init__(self, path, session_url, chunk_size_bytes, transport=None):
        self.path = path
        self.total = os.path.getsize(path)
        self.chunk_size = max(256 * 1024, int(chunk_size_bytes))
        self.transport = transport or GcsResumableTransport(session_url)

    def upload(self, on_progress=None, is_cancelled=None, sleep=time.sleep):
        """Send the file, resuming if the store already holds part of it.

        :param on_progress: called as ``on_progress(sent, total)`` between chunks
        :param is_cancelled: called between chunks; truthy aborts the upload
        :raises UploadCancelled: when ``is_cancelled`` said so
        :raises UploadFailed: when the store refused, or retries ran out
        """
        offset = self._committed_offset()
        if on_progress:
            on_progress(offset, self.total)

        with open(self.path, "rb") as handle:
            while offset < self.total:
                if is_cancelled and is_cancelled():
                    raise UploadCancelled()

                handle.seek(offset)
                chunk = handle.read(self.chunk_size)
                if not chunk:
                    # The file shrank under us — refusing beats sending a
                    # truncated object the catalogue would then call complete.
                    raise UploadFailed("The file changed while it was being sent.")

                result = self._send_with_retry(chunk, offset, sleep)
                if result.complete:
                    offset = self.total
                    break

                # Trust the store's count, not ours: it is the only one that
                # knows what actually landed.
                offset = result.committed if result.committed is not None else offset + len(chunk)
                if on_progress:
                    on_progress(offset, self.total)

        if on_progress:
            on_progress(self.total, self.total)
        return True

    # -- internals ----------------------------------------------------------

    def _committed_offset(self):
        """Ask the store what it already holds. Zero on a fresh session."""
        result = self.transport.send(_PROBE, 0, self.total)
        if result.error:
            raise UploadFailed(result.error)
        if result.complete:
            return self.total
        return result.committed or 0

    def _send_with_retry(self, chunk, offset, sleep):
        last_reason = None
        for attempt in range(MAX_CHUNK_ATTEMPTS):
            if attempt > 0:
                sleep(RETRY_BACKOFF_SEC * (2 ** (attempt - 1)))
                # Re-ask before resending: a chunk may well have landed even
                # though the answer never came back to us.
                probe = self.transport.send(_PROBE, 0, self.total)
                if probe.error:
                    raise UploadFailed(probe.error)
                if probe.complete:
                    return ChunkResult(committed=self.total, complete=True)
                if (probe.committed or 0) > offset:
                    return ChunkResult(committed=probe.committed)

            result = self.transport.send(chunk, offset, self.total)
            if result.error:
                # A refusal is a verdict, not a hiccup — retrying an expired
                # session only wastes the user's time.
                raise UploadFailed(result.error)
            if result.complete or result.committed is not None:
                return result
            last_reason = "the connection dropped"

        raise UploadFailed(
            "The transfer stopped after {} attempts ({}). It can be resumed by publishing again.".format(
                MAX_CHUNK_ATTEMPTS, last_reason or "no response from the server"
            )
        )
