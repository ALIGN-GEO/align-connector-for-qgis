#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove that a large upload survives what large uploads actually meet.

    python scripts/smoke_uploader.py

Runs on a plain interpreter — no QGIS, no network. The transport is injected,
so what is exercised is the part that decides: where to resume from, when to
retry, when to stop. A store that "drops the connection" here is a store that
dropped it for real, minus the three hours of waiting.

The scenarios are the ones that cost users their evening:

* a clean transfer, in chunks, with progress;
* a connection lost mid-file, then resumed — from the store's count, not ours;
* a session that expired, refused immediately instead of retried;
* a cancellation, honoured between chunks;
* a store that already holds everything (a retry after a lost final answer).

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from align_connector.publish.uploader import (  # noqa: E402
    ChunkResult,
    ResumableUploader,
    UploadCancelled,
    UploadFailed,
    _PROBE,
)

CHUNK = 256 * 1024
TOTAL = CHUNK * 5 + 1234  # deliberately not a whole number of chunks


class FakeStore:
    """An object store that holds bytes, and can misbehave on cue."""

    def __init__(self, drop_at=None, expire_after=None, holds=0):
        self.held = holds
        self.drop_at = drop_at            # offset at which the connection dies once
        self.expire_after = expire_after  # number of calls before the session dies
        self.calls = 0
        self.dropped = False

    def send(self, data, start, total):
        self.calls += 1
        if self.expire_after is not None and self.calls > self.expire_after:
            return ChunkResult(error="The upload session expired. Start the publish again.")

        if data is _PROBE:
            if self.held >= total:
                return ChunkResult(committed=total, complete=True)
            return ChunkResult(committed=self.held)

        if self.drop_at is not None and start == self.drop_at and not self.dropped:
            self.dropped = True
            return ChunkResult()  # no verdict: the connection died mid-flight

        # A store only accepts bytes that continue what it already holds.
        if start != self.held:
            return ChunkResult(committed=self.held)
        self.held = start + len(data)
        if self.held >= total:
            return ChunkResult(committed=total, complete=True)
        return ChunkResult(committed=self.held)


def make_file():
    handle = tempfile.NamedTemporaryFile(delete=False, suffix='.tif')
    handle.write(os.urandom(TOTAL))
    handle.close()
    return handle.name


def check(label, condition, detail=""):
    if condition:
        print("  ok   {}".format(label))
        return True
    print("  FAIL {} {}".format(label, detail))
    return False


def main():
    path = make_file()
    failures = 0
    try:
        # -- 1. clean transfer -------------------------------------------------
        print("clean transfer")
        store = FakeStore()
        seen = []
        ResumableUploader(path, "s://session", CHUNK, transport=store).upload(
            on_progress=lambda sent, total: seen.append(sent)
        )
        failures += not check("every byte arrived", store.held == TOTAL, "(held %d)" % store.held)
        failures += not check("progress was reported", len(seen) > 3, "(%d updates)" % len(seen))
        failures += not check("progress ends at the total", seen[-1] == TOTAL)

        # -- 2. connection lost, then resumed ---------------------------------
        print("connection lost mid-file")
        store = FakeStore(drop_at=CHUNK * 2)
        ResumableUploader(path, "s://session", CHUNK, transport=store).upload(sleep=lambda _: None)
        failures += not check("the transfer completed anyway", store.held == TOTAL)
        failures += not check("the drop really happened", store.dropped)

        # -- 3. resuming a partly-uploaded file --------------------------------
        print("resuming what the store already holds")
        store = FakeStore(holds=CHUNK * 3)
        first = []
        ResumableUploader(path, "s://session", CHUNK, transport=store).upload(
            on_progress=lambda sent, total: first.append(sent)
        )
        failures += not check("it resumed from the store's count", first[0] == CHUNK * 3,
                              "(started at %d)" % first[0])
        failures += not check("and finished", store.held == TOTAL)

        # -- 4. expired session ------------------------------------------------
        print("expired session")
        store = FakeStore(expire_after=1)
        try:
            ResumableUploader(path, "s://session", CHUNK, transport=store).upload(sleep=lambda _: None)
            failures += not check("refused an expired session", False, "(it did not raise)")
        except UploadFailed as exc:
            failures += not check("refused, with a sentence a human can act on",
                                  "expired" in str(exc).lower())
            failures += not check("without burning retries on it", store.calls <= 3,
                                  "(%d calls)" % store.calls)

        # -- 5. cancellation ---------------------------------------------------
        print("cancellation")
        store = FakeStore()
        try:
            ResumableUploader(path, "s://session", CHUNK, transport=store).upload(
                is_cancelled=lambda: store.held >= CHUNK * 2
            )
            failures += not check("stopped when asked", False, "(it did not raise)")
        except UploadCancelled:
            failures += not check("stopped when asked", True)
            failures += not check("and left the rest unsent", store.held < TOTAL)

        # -- 6. the store already has everything -------------------------------
        print("store already complete")
        store = FakeStore(holds=TOTAL)
        ResumableUploader(path, "s://session", CHUNK, transport=store).upload()
        failures += not check("no bytes were re-sent", store.calls == 1, "(%d calls)" % store.calls)

    finally:
        os.unlink(path)

    print()
    if failures:
        print("%d check(s) failed" % failures)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
