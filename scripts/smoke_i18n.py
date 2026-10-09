# -*- coding: utf-8 -*-
"""The shipped translation, loaded by a real QTranslator (TCK-2183).

Needs Qt: run it with the Python of a QGIS install
(``python-qgis-ltr.bat scripts/smoke_i18n.py`` on Windows, or inside the
``qgis/qgis`` image in CI). It proves what the pure-Python check cannot:
that the ``.qm`` written by ``scripts/translations.py`` is a file Qt reads,
sentence for sentence, and that the plugin's ``install`` follows QGIS's
language and stays silent for a language without a file.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from qgis.PyQt.QtCore import QCoreApplication, QTranslator  # noqa: E402

import translations  # noqa: E402

failures = []


def check(label, condition, detail=""):
    print("{}  {}{}".format("ok " if condition else "BAD", label, "  [{}]".format(detail) if detail else ""))
    if not condition:
        failures.append(label)


app = QCoreApplication.instance() or QCoreApplication(sys.argv)

# -- every translation of the .ts comes back through Qt ------------------------
entries = translations.read_ts(translations.ts_path("fr"))
translated = {src: text for src, (text, finished) in entries.items() if text and finished}
translator = QTranslator()
check("the shipped .qm loads", translator.load(str(translations.qm_path("fr"))))
QCoreApplication.installTranslator(translator)
wrong = [src for src, fr in translated.items() if QCoreApplication.translate(translations.CONTEXT, src) != fr]
check("every sentence translates back ({} of them)".format(len(translated)), not wrong, wrong[:3])
check("a sentence outside the file stays English",
      QCoreApplication.translate(translations.CONTEXT, "not a plugin sentence") == "not a plugin sentence")
check("another context is untouched", QCoreApplication.translate("Other", "Publish") == "Publish")
QCoreApplication.removeTranslator(translator)

# -- the plugin's own loader ---------------------------------------------------
from align_connector import i18n  # noqa: E402

plugin_dir = os.path.join(ROOT, "align_connector")
check("the language code is two letters", len(i18n.language()) == 2, i18n.language())

# Force the language through a fake plugin dir: fr has a file, nl has none.
with tempfile.TemporaryDirectory() as scratch:
    os.makedirs(os.path.join(scratch, i18n.FOLDER))
    with open(translations.qm_path("fr"), "rb") as src, open(i18n.translation_file(scratch, "fr"), "wb") as dst:
        dst.write(src.read())
    original = i18n.language
    try:
        i18n.language = lambda: "fr"
        check("fr installs and the panel speaks French", i18n.install(scratch) == "fr" and i18n.tr("Publish") == "Publier")
        i18n.uninstall()
        check("uninstall brings English back", i18n.tr("Publish") == "Publish")
        i18n.language = lambda: "nl"
        check("a language without a file installs nothing", i18n.install(scratch) is None and i18n.tr("Publish") == "Publish")
        i18n.language = lambda: "en"
        check("English installs nothing", i18n.install(scratch) is None)
    finally:
        i18n.language = original
        i18n.uninstall()

print("")
print("{} failure(s)".format(len(failures)))
sys.exit(1 if failures else 0)
