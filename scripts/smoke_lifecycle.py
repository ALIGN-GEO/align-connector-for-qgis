#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Load the plugin the way QGIS does, and check it cleans up after itself.

Run it with the Python that ships *with* QGIS — it needs the real ``qgis.PyQt``
shim and a real Qt:

    "C:/Program Files/QGIS 3.44.10/bin/python-qgis-ltr.bat" scripts/smoke_lifecycle.py

What it proves, and why that is worth a script rather than a manual click:

* ``classFactory`` returns a plugin, i.e. the deferred import inside it holds.
* ``initGui`` registers exactly what we expect, on the real Qt classes.
* ``unload`` removes **everything** it registered. This is the one QGIS
  contract that fails silently: a plugin that under-cleans looks fine until a
  reload, then shows duplicated menu entries wired to a dead object.

It cannot replace opening QGIS once — nothing here draws a pixel — but it
catches the whole class of errors that only appear at load time.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import pathlib
import sys
from unittest.mock import MagicMock

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> int:
    from qgis.PyQt.QtWidgets import QApplication, QWidget

    # Qt objects need a running application instance; QGIS provides one, we
    # must stand one up ourselves.
    app = QApplication.instance() or QApplication([])

    from align_connector import classFactory

    iface = MagicMock()
    # Qt refuses a mock where it expects a real parent, so the one call whose
    # return value crosses into Qt gets a real widget. Everything else stays a
    # mock — that is what lets us assert on what was registered.
    main_window = QWidget()
    iface.mainWindow.return_value = main_window

    plugin = classFactory(iface)
    if plugin is None:
        print("FAIL: classFactory returned None")
        return 1

    plugin.initGui()

    if not iface.addPluginToWebMenu.called:
        print("FAIL: initGui did not add a menu entry")
        return 1
    if not iface.addToolBarIcon.called:
        print("FAIL: initGui did not add a toolbar icon")
        return 1

    menu_title, action = iface.addPluginToWebMenu.call_args[0]
    if not action.text():
        print("FAIL: the registered action has no label")
        return 1
    if action.icon().isNull():
        print("FAIL: the registered action has no icon — icon.svg did not load")
        return 1

    plugin.unload()

    # Symmetry is the point: as many removals as additions, on the same action.
    if iface.removePluginWebMenu.call_count != iface.addPluginToWebMenu.call_count:
        print("FAIL: unload did not remove every menu entry initGui added")
        return 1
    if iface.removeToolBarIcon.call_count != iface.addToolBarIcon.call_count:
        print("FAIL: unload did not remove every toolbar icon initGui added")
        return 1
    if iface.removePluginWebMenu.call_args[0][1] is not action:
        print("FAIL: unload removed a different action than the one registered")
        return 1

    # A second unload must not raise: QGIS may call it more than once.
    plugin.unload()

    print(
        "OK — loads, registers '%s' under %s, and unloads cleanly (Qt %s)"
        % (action.text(), menu_title, app.instance().applicationVersion() or "runtime")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
