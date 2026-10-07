# -*- coding: utf-8 -*-
"""The ALIGN panel: publish one layer, several, or the whole project, and
watch each publication (TCK-2198, ADR-0058 §5).

Nothing is re-exported here on purpose. `dock`, `publish_view` and
`activity_view` need a running QGIS; `batch` and `outcome` do not, and that
is what lets their decisions be proven on a plain interpreter.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
