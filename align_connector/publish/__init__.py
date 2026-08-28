# -*- coding: utf-8 -*-
"""Publishing: sending a layer to an ALIGN Datastore.

Nothing is re-exported here on purpose. `dialog` and `task` need a running
QGIS; `uploader` does not, and that is what lets its behaviour — where to
resume, when to retry, when to stop — be exercised on a plain interpreter, in
CI, in seconds. A convenience import in this file would have quietly taken that
away.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
