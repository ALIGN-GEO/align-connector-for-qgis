# -*- coding: utf-8 -*-
"""Follow a lot to its end: the N publications, then what comes after.

A lot is N tasks that finish in any order. Something has to notice when the
last one has, and know which request each task carried: that is all this
does. Pure: proven by ``scripts/smoke_webmap.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""


class LotFollower(object):
    """Pairs each task with its request and reports once every task is done."""

    def __init__(self, batch, on_all_finished):
        self.batch = batch
        self._on_all_finished = on_all_finished
        #: Request per task, in the order the tasks were started.
        self._requests = []
        self._pending = set()
        self._results = {}

    def started(self, task, request):
        self._requests.append((task, request))
        self._pending.add(id(task))

    def finished(self, task, succeeded):
        """Record one outcome; the callback fires on the last one only."""
        if id(task) not in self._pending:
            return
        self._pending.discard(id(task))
        self._results[id(task)] = succeeded
        if not self._pending:
            self._on_all_finished(self)

    @property
    def done(self):
        return not self._pending and bool(self._requests)

    def records(self):
        """``(request, task, succeeded)`` in the order the tasks were started."""
        return [(request, task, self._results.get(id(task), False)) for task, request in self._requests]
