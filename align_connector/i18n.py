# -*- coding: utf-8 -*-
"""Every sentence a user reads goes through here.

No translation ships yet; wiring the call now is what makes shipping one
later a matter of files, not of revisiting every string (TCK-2183). The
sentence is translated first and formatted after, so a translator sees
``"{} features will be written"`` and not a number.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.PyQt.QtCore import QCoreApplication

#: The translation context, shared by every module: one file per language.
CONTEXT = "AlignConnector"


def tr(text):
    return QCoreApplication.translate(CONTEXT, text)
