#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Load the plugin the way QGIS does, and check it cleans up after itself.

Run it with the Python that ships *with* QGIS — it needs the real ``qgis.PyQt``
shim and a real Qt:

    "C:/Program Files/QGIS 3.44.10/bin/python-qgis-ltr.bat" scripts/smoke_lifecycle.py

What it proves, and why that is worth a script rather than a manual click:

* ``classFactory`` returns a plugin, i.e. the deferred import inside it holds.
* ``initGui`` registers exactly what we expect, on the real Qt classes.
* ``unload`` removes **everything** it registered: menu entries, toolbar
  icon, the ALIGN dock and the layer context-menu entries. This is the one
  QGIS contract that fails silently: a plugin that under-cleans looks fine
  until a reload, then shows duplicated entries wired to a dead object.
* The ALIGN panel builds on the real Qt classes (QgsDockWidget and the
  widgets of its two views), which no stub can prove.

It cannot replace opening QGIS once — nothing here draws a pixel — but it
catches the whole class of errors that only appear at load time.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import pathlib
import sys
from unittest.mock import MagicMock

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Keeps the QApplication alive for the duration of the check.
_APP = None
sys.path.insert(0, str(REPO))


def main() -> int:
    from qgis.PyQt.QtWidgets import QApplication, QWidget

    # Qt objects need a running application instance; QGIS provides one, we
    # must stand one up ourselves — and keep it referenced for the whole run,
    # because Qt tears the application down when it is garbage-collected.
    global _APP
    _APP = QApplication.instance() or QApplication([])

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
    if not iface.addToolBarWidget.called:
        print("FAIL: initGui did not add the toolbar button")
        return 1

    menu_title = iface.addPluginToWebMenu.call_args[0][0]
    registered = [call[0][1] for call in iface.addPluginToWebMenu.call_args_list]
    if any(not a.text() for a in registered):
        print("FAIL: a registered action has no label")
        return 1

    # The toolbar carries one button (TCK-2216): its default action is the
    # publish one, with the icon; its menu holds the three others.
    from qgis.PyQt.QtWidgets import QToolButton

    button = iface.addToolBarWidget.call_args[0][0]
    if not isinstance(button, QToolButton) or button.defaultAction() is None:
        print("FAIL: the toolbar widget is not a tool button with a default action")
        return 1
    toolbar_action = button.defaultAction()
    if toolbar_action.icon().isNull():
        print("FAIL: the toolbar action has no icon — icon.png did not load")
        return 1
    if button.menu() is None or len([a for a in button.menu().actions() if not a.isSeparator()]) != 4:
        print("FAIL: the toolbar button's menu does not hold the four actions, publish included")
        return 1
    action = toolbar_action

    # The panel: one dock, named so QGIS can remember its place, hidden until
    # asked for; and one context-menu entry per layer type it can publish.
    if iface.addDockWidget.call_count != 1:
        print("FAIL: initGui must add exactly one dock widget")
        return 1
    dock = iface.addDockWidget.call_args[0][1]
    if not dock.objectName():
        print("FAIL: the dock has no object name — QGIS could not remember its place")
        return 1
    if dock.isVisible():
        print("FAIL: the dock must stay hidden until the user asks for it")
        return 1
    if iface.addCustomActionForLayerType.call_count == 0:
        print("FAIL: no 'Publish to ALIGN…' entry in the layer context menu")
        return 1
    layer_actions = [call[0][0] for call in iface.addCustomActionForLayerType.call_args_list]

    plugin.unload()

    if iface.removeDockWidget.call_count != 1 or iface.removeDockWidget.call_args[0][0] is not dock:
        print("FAIL: unload did not remove the dock it added")
        return 1
    if iface.removeCustomActionForLayerType.call_count != len(layer_actions):
        print("FAIL: unload did not remove every layer context-menu entry initGui added")
        return 1
    removed_layer_actions = [call[0][0] for call in iface.removeCustomActionForLayerType.call_args_list]
    if set(id(a) for a in removed_layer_actions) != set(id(a) for a in layer_actions):
        print("FAIL: unload removed different context-menu entries than initGui added")
        return 1

    # Symmetry is the point: as many removals as additions, on the same action.
    if iface.removePluginWebMenu.call_count != iface.addPluginToWebMenu.call_count:
        print("FAIL: unload did not remove every menu entry initGui added")
        return 1
    # addToolBarWidget hands back the widget's action; that is what unload removes.
    if iface.removeToolBarIcon.call_count != iface.addToolBarWidget.call_count:
        print("FAIL: unload did not remove the toolbar button initGui added")
        return 1
    removed = [call[0][1] for call in iface.removePluginWebMenu.call_args_list]
    if set(id(a) for a in removed) != set(id(a) for a in registered):
        print("FAIL: unload removed a different set of actions than initGui registered")
        return 1

    # A second unload must not raise: QGIS may call it more than once.
    plugin.unload()

    print(
        "OK — loads, registers %d action(s) under %s (toolbar: '%s'), one dock, "
        "%d layer context entr%s, unloads cleanly"
        % (len(registered), menu_title, action.text(), len(layer_actions), "y" if len(layer_actions) == 1 else "ies")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
