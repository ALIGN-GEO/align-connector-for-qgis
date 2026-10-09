# -*- coding: utf-8 -*-
"""Every sentence a user reads goes through here.

The sentence is translated first and formatted after, so a translator sees
``"{} features will be written"`` and not a number. English is the source
language; a shipped ``i18n/align_connector_<lang>.qm`` is what makes the
plugin speak another one (TCK-2183: FR first). The language followed is the
one QGIS itself runs in, and a language without a file reads as English,
silently.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os

from qgis.PyQt.QtCore import QCoreApplication

#: The translation context, shared by every module: one file per language.
CONTEXT = "AlignConnector"

#: Folder and file pattern of the shipped translations, relative to the plugin.
FOLDER = "i18n"
FILE_PATTERN = "align_connector_{}.qm"

#: The translator currently installed, kept alive: Qt only borrows it.
_translator = None


def tr(text):
    return QCoreApplication.translate(CONTEXT, text)


def language():
    """The language QGIS displays itself in, as a two-letter code.

    QGIS resolves its own locale (the user's override, else the system's) and
    publishes it; following that value is what makes the plugin agree with
    the menus around it. ``en`` when nothing is known.
    """
    from qgis.core import QgsApplication

    locale = QgsApplication.locale() or ""
    code = locale.replace("-", "_").split("_")[0].strip().lower()
    return code or "en"


def translation_file(plugin_dir, code):
    return os.path.join(plugin_dir, FOLDER, FILE_PATTERN.format(code))


def install(plugin_dir):
    """Install the translation matching QGIS's language, when one ships.

    :returns: the language code installed, or ``None`` for English (the
        source language, or a language without a file)
    """
    from qgis.PyQt.QtCore import QTranslator

    global _translator
    uninstall()
    code = language()
    path = translation_file(plugin_dir, code)
    if code == "en" or not os.path.exists(path):
        return None
    translator = QTranslator()
    if not translator.load(path):
        return None
    QCoreApplication.installTranslator(translator)
    _translator = translator
    return code


def uninstall():
    """Take the translation back out, so a reload never stacks two."""
    global _translator
    if _translator is not None:
        QCoreApplication.removeTranslator(_translator)
        _translator = None
