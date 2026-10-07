# -*- coding: utf-8 -*-
"""One source runs on Qt5 and Qt6: the enum lookup that makes it possible.

Qt6 scoped its enums (``Qt.DockWidgetArea.RightDockWidgetArea``) where Qt5
exposed them flat (``Qt.RightDockWidgetArea``). Importing through ``qgis.PyQt``
hides the binding; looking members up by name hides the scoping. Every module
that touches a Qt enum goes through here, so the day QGIS 4 (Qt6) is the
reference (TCK-2180), nothing has to be rewritten.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""


def qt_enum(owner, group, member):
    """``owner.group.member`` on Qt6, ``owner.member`` on Qt5."""
    scope = getattr(owner, group, owner)
    return getattr(scope, member)


def run_exec(target):
    """Run a dialog or an event loop on both bindings.

    PyQt5 named it ``exec_`` (``exec`` was a keyword in the Python 2 days);
    PyQt6 dropped the underscore.
    """
    runner = getattr(target, "exec", None) or getattr(target, "exec_")
    return runner()
