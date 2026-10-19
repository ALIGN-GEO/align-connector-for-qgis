# -*- coding: utf-8 -*-
"""Stand-ins for the QGIS and Qt names the plugin imports at load time.

The smoke scripts run on a plain interpreter, with no QGIS installed: they
prove decisions, not rendering. Importing a plugin module pulls ``qgis.core``
and ``qgis.PyQt`` in, so this installs just enough of both for the import to
succeed — every name is an empty class unless a module needs more.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import sys
import types

_CORE = (
    "Qgis",
    "QgsApplication",
    "QgsBlockingNetworkRequest",
    "QgsCoordinateReferenceSystem",
    "QgsCoordinateTransform",
    "QgsCoordinateTransformContext",
    "QgsFeature",
    "QgsFields",
    "QgsField",
    "QgsMessageLog",
    "QgsNetworkAccessManager",
    "QgsPointCloudLayer",
    "QgsProject",
    "QgsRasterLayer",
    "QgsTiledSceneLayer",
    "QgsVectorTileLayer",
    "QgsRasterPipe",
    "QgsRenderContext",
    "QgsTask",
    "QgsVectorLayer",
    "QgsWkbTypes",
)

_QTWIDGETS = (
    "QApplication",
    "QCheckBox",
    "QComboBox",
    "QDialog",
    "QDialogButtonBox",
    "QFormLayout",
    "QLabel",
    "QLineEdit",
    "QPlainTextEdit",
    "QSizePolicy",
    "QVBoxLayout",
)


def install():
    """Register the stub modules in ``sys.modules``. Idempotent."""
    if "qgis" in sys.modules and getattr(sys.modules["qgis"], "_align_stub", False):
        return

    core = types.ModuleType("qgis.core")
    for name in _CORE:
        setattr(core, name, type(name, (object,), {}))

    class QgsVectorFileWriter(object):
        NoError = 0

    class QgsRasterFileWriter(object):
        NoError = 0

        def __init__(self, path):
            self.path = path

    core.QgsVectorFileWriter = QgsVectorFileWriter
    core.QgsRasterFileWriter = QgsRasterFileWriter

    class _Registry(object):
        """QGIS decodes `key=value&key=value` URIs; the stub reads them the same way."""

        @staticmethod
        def decodeUri(provider, uri):  # noqa: N802 — QGIS's name
            # A data-source URI (`key='value'`) is a dialect this stand-in does
            # not read: it answers nothing, as an older QGIS would, and the
            # plugin's own plain reader takes over.
            if "='" in (uri or "") or '="' in (uri or ""):
                return {}
            decoded = {}
            for part in (uri or "").split("&"):
                if "=" in part:
                    key, value = part.split("=", 1)
                    decoded[key.strip()] = value.strip()
            return decoded

    class QgsProviderRegistry(object):
        @staticmethod
        def instance():
            return _Registry()

    core.QgsProviderRegistry = QgsProviderRegistry
    # A smoke names a geometry type by its WKT word; QGIS answers the same question on its enum.
    core.QgsWkbTypes.isCurvedType = staticmethod(
        lambda wkb: str(wkb).startswith(("CircularString", "CompoundCurve", "CurvePolygon", "MultiCurve", "MultiSurface"))
    )

    qtcore = types.ModuleType("qgis.PyQt.QtCore")
    qtcore.QVariant = type("QVariant", (object,), {"Map": 8, "List": 9, "StringList": 11, "Hash": 28})
    qtcore.Qt = type("Qt", (object,), {"PlainText": 0, "WaitCursor": 3})
    qtcore.QSettings = type("QSettings", (object,), {"value": lambda self, key, default=None: default})
    # No translator on a plain interpreter: a sentence comes back as written.
    qtcore.QCoreApplication = type(
        "QCoreApplication", (object,), {"translate": staticmethod(lambda context, text, *args: text)}
    )
    for name in ("QByteArray", "QEventLoop", "QUrl", "QThread", "QTimer"):
        setattr(qtcore, name, type(name, (object,), {}))

    class _BoundSignal(object):
        def __init__(self):
            self._slots = []

        def connect(self, slot):
            self._slots.append(slot)

        def emit(self, *args):
            for slot in list(self._slots):
                slot(*args)

    class pyqtSignal(object):  # noqa: N801 — Qt's name
        """A signal per instance, as Qt gives: connect, emit, nothing queued."""

        def __init__(self, *_types):
            self._name = None

        def __set_name__(self, owner, name):
            self._name = "_signal_" + name

        def __get__(self, instance, owner):
            if instance is None:
                return self
            bound = instance.__dict__.get(self._name)
            if bound is None:
                bound = instance.__dict__[self._name] = _BoundSignal()
            return bound

    class QObject(object):
        def __init__(self, parent=None):
            self._parent = parent

    qtcore.pyqtSignal = pyqtSignal
    qtcore.QObject = QObject

    gui = types.ModuleType("qgis.PyQt.QtGui")
    gui.QDesktopServices = type("QDesktopServices", (object,), {"openUrl": staticmethod(lambda url: True)})

    widgets = types.ModuleType("qgis.PyQt.QtWidgets")
    for name in _QTWIDGETS:
        setattr(widgets, name, type(name, (object,), {}))

    network = types.ModuleType("qgis.PyQt.QtNetwork")
    for name in ("QNetworkAccessManager", "QNetworkReply", "QNetworkRequest"):
        setattr(network, name, type(name, (object,), {}))

    qgis = types.ModuleType("qgis")
    qgis._align_stub = True
    pyqt = types.ModuleType("qgis.PyQt")
    qgis.core = core
    qgis.PyQt = pyqt
    pyqt.QtCore = qtcore
    pyqt.QtWidgets = widgets
    pyqt.QtNetwork = network
    pyqt.QtGui = gui

    sys.modules.update(
        {
            "qgis": qgis,
            "qgis.core": core,
            "qgis.PyQt": pyqt,
            "qgis.PyQt.QtCore": qtcore,
            "qgis.PyQt.QtWidgets": widgets,
            "qgis.PyQt.QtNetwork": network,
            "qgis.PyQt.QtGui": gui,
        }
    )
