# -*- coding: utf-8 -*-
"""Waiting when ALIGN asks to (TCK-2296, defect 69).

A click that publishes many layers opens one upload session per layer, all
at once, and ALIGN lets one person open only so many a minute. Its 429 says
when to come back (``Retry-After``): the layer waits that long and asks
again, instead of failing a lot that only had to wait a minute. ALIGN's
ceiling stays what it is; the plugin keeps to it.

Pure: proven by ``scripts/smoke_capacity.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from ..api import RATE_LIMITED, ApiError
from .uploader import UploadCancelled

#: The longest one layer waits for ALIGN, in all, before the refusal is the user's.
MAX_WAIT_SECONDS = 600


def paced(call, cancelled, sleep, max_wait=MAX_WAIT_SECONDS):
    """``call()``, asked again after the wait ALIGN names in a 429.

    :param cancelled: ``() -> bool``, read every second of a wait
    :param sleep: ``(seconds) -> None``
    A pace refusal is a 429, or any refusal coded RATE_LIMITED (ALIGN's 403
    once it bans, defect 71).

    :raises ApiError: any other refusal, a pace refusal that names no wait, or one past ``max_wait``
    :raises UploadCancelled: cancelled while waiting
    """
    waited = 0
    while True:
        try:
            return call()
        except ApiError as exc:
            delay = exc.retry_after if exc.status == 429 or exc.code == RATE_LIMITED else None
            if delay is None or waited + delay > max_wait:
                raise
        for _ in range(delay):
            if cancelled():
                raise UploadCancelled()
            sleep(1)
        waited += delay
