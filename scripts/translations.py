# -*- coding: utf-8 -*-
"""The plugin's translations, end to end: extract, compile, check (TCK-2183).

    python scripts/translations.py extract           # refresh i18n/*.ts from the source
    python scripts/translations.py compile           # write i18n/*.qm next to each .ts
    python scripts/translations.py check             # CI gate: complete and compiled

Three decisions, so the tool stays small and dependency-free:

* **Extraction reads the Python AST**, not regexes: every ``tr("…")``,
  ``self.tr("…")`` and ``QCoreApplication.translate(CONTEXT, "…")`` call,
  with the implicit concatenation Python already folded. One translation
  context for the whole plugin (``i18n.CONTEXT``): the modules share it at
  runtime, so the file shares it too.
* **The ``.ts`` file is Qt Linguist's own format** (TS 2.1): a translator may
  open it in Linguist; the ``<location>`` lines say where a sentence lives.
  An existing translation survives an extraction; a sentence that left the
  source leaves the file.
* **The ``.qm`` is written here**, in the binary layout ``QTranslator`` reads
  (magic, hash table, messages, language), because no ``lrelease`` ships
  with QGIS on Windows and pulling a Qt toolchain for a 60 KB file would be
  the heavier dependency. ``scripts/smoke_i18n.py`` proves the layout against
  a real ``QTranslator``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import ast
import pathlib
import struct
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "align_connector"
I18N = PACKAGE / "i18n"
CONTEXT = "AlignConnector"
#: The languages shipped. English is the source language and has no file.
LANGUAGES = ("fr",)


# -- extraction ---------------------------------------------------------------

def _string_of(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_tr_call(node):
    """``tr(...)``, ``self.tr(...)`` or ``QCoreApplication.translate(CONTEXT, ...)``."""
    func = node.func
    if isinstance(func, ast.Name) and func.id == "tr":
        return node.args[:1]
    if isinstance(func, ast.Attribute) and func.attr == "tr":
        return node.args[:1]
    if isinstance(func, ast.Attribute) and func.attr == "translate" and len(node.args) >= 2:
        first = node.args[0]
        if (isinstance(first, ast.Name) and first.id == "CONTEXT") or _string_of(first) == CONTEXT:
            return node.args[1:2]
    return None


def extract():
    """Every source sentence, with the places it is used: ``{text: [(file, line), …]}``."""
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            args = _is_tr_call(node)
            if not args:
                continue
            text = _string_of(args[0])
            if text is None:
                if isinstance(node.func, ast.Attribute) and node.func.attr == "translate":
                    # The two `tr` wrappers themselves forward a variable.
                    continue
                raise SystemExit("{}:{}: tr() needs a literal sentence, not an expression".format(rel, node.lineno))
            found.setdefault(text, []).append((rel, node.lineno))
    return found


# -- the .ts file -------------------------------------------------------------

def ts_path(code):
    return I18N / "align_connector_{}.ts".format(code)


def qm_path(code):
    return I18N / "align_connector_{}.qm".format(code)


def read_ts(path):
    """``{source: (translation, finished)}`` of a .ts file, or ``{}`` when absent."""
    if not path.exists():
        return {}
    out = {}
    for message in ET.parse(str(path)).getroot().iter("message"):
        source = message.findtext("source") or ""
        node = message.find("translation")
        text = (node.text or "") if node is not None else ""
        finished = node is not None and node.get("type") not in ("unfinished", "obsolete", "vanished")
        out[source] = (text, finished)
    return out


def write_ts(path, code, sources, existing):
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!DOCTYPE TS>",
        '<TS version="2.1" language="{}" sourcelanguage="en">'.format(code),
        "<context>",
        "    <name>{}</name>".format(CONTEXT),
    ]
    for text in sorted(sources):
        translation, finished = existing.get(text, ("", False))
        lines.append("    <message>")
        for rel, line in sources[text]:
            lines.append('        <location filename="../../{}" line="{}"/>'.format(rel, line))
        lines.append("        <source>{}</source>".format(escape(text)))
        if translation and finished:
            lines.append("        <translation>{}</translation>".format(escape(translation)))
        else:
            lines.append('        <translation type="unfinished">{}</translation>'.format(escape(translation)))
        lines.append("    </message>")
    lines += ["</context>", "</TS>", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# -- the .qm file -------------------------------------------------------------

#: The 16 bytes every .qm starts with (checked against QGIS's own qgis_fr.qm).
MAGIC = bytes((0x3C, 0xB8, 0x64, 0x18, 0xCA, 0xEF, 0x9C, 0x95, 0xCD, 0x21, 0x1C, 0xBF, 0x60, 0xA1, 0xBD, 0xDD))
TAG_END, TAG_TRANSLATION, TAG_SOURCE_TEXT, TAG_CONTEXT, TAG_COMMENT = 1, 3, 6, 7, 8
BLOCK_HASHES, BLOCK_MESSAGES, BLOCK_LANGUAGE = 0x42, 0x69, 0xA7


def elf_hash(data):
    """Qt's ``elfHash`` over the UTF-8 bytes of ``source + comment``."""
    h = 0
    for byte in data:
        h = ((h << 4) + byte) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g & 0xFFFFFFFF
    return h or 1


def compile_qm(messages, code):
    """The bytes of a ``.qm`` holding ``messages`` (``[(source, translation), …]``)."""
    body = bytearray()
    index = []
    for source, translation in messages:
        offset = len(body)
        utf16 = translation.encode("utf-16-be")
        body += bytes((TAG_TRANSLATION,)) + struct.pack(">I", len(utf16)) + utf16
        # lrelease writes the (empty) comment before the source; same order here.
        body += bytes((TAG_COMMENT,)) + struct.pack(">I", 0)
        src = source.encode("utf-8")
        body += bytes((TAG_SOURCE_TEXT,)) + struct.pack(">I", len(src)) + src
        ctx = CONTEXT.encode("utf-8")
        body += bytes((TAG_CONTEXT,)) + struct.pack(">I", len(ctx)) + ctx
        body += bytes((TAG_END,))
        index.append((elf_hash(src), offset))
    index.sort()
    hashes = b"".join(struct.pack(">II", h, o) for h, o in index)

    def block(tag, data):
        return bytes((tag,)) + struct.pack(">I", len(data)) + data

    return MAGIC + block(BLOCK_HASHES, hashes) + block(BLOCK_MESSAGES, bytes(body)) + block(BLOCK_LANGUAGE, code.encode("utf-8"))


# -- commands -----------------------------------------------------------------

def cmd_extract():
    sources = extract()
    for code in LANGUAGES:
        write_ts(ts_path(code), code, sources, read_ts(ts_path(code)))
        print("{}: {} sentences".format(ts_path(code).name, len(sources)))


def cmd_compile():
    for code in LANGUAGES:
        entries = read_ts(ts_path(code))
        messages = [(source, text) for source, (text, finished) in entries.items() if text and finished]
        qm_path(code).write_bytes(compile_qm(messages, code))
        print("{}: {} translations".format(qm_path(code).name, len(messages)))


def cmd_check():
    """Fail when a sentence is missing from the .ts, untranslated, or the .qm is stale."""
    problems = []
    sources = extract()
    for code in LANGUAGES:
        entries = read_ts(ts_path(code))
        for text in sources:
            if text not in entries:
                problems.append("{}: not in the .ts: {!r}".format(code, text))
            elif not entries[text][0] or not entries[text][1]:
                problems.append("{}: untranslated: {!r}".format(code, text))
        for text in entries:
            if text not in sources:
                problems.append("{}: no longer in the source: {!r}".format(code, text))
        messages = [(source, text) for source, (text, finished) in entries.items() if text and finished]
        expected = compile_qm(messages, code)
        if not qm_path(code).exists() or qm_path(code).read_bytes() != expected:
            problems.append("{}: .qm is stale, run `python scripts/translations.py compile`".format(code))
    for problem in problems:
        print("FAIL", problem)
    print("{} sentences, {} language(s), {} problem(s)".format(len(sources), len(LANGUAGES), len(problems)))
    return 1 if problems else 0


def main(argv):
    commands = {"extract": cmd_extract, "compile": cmd_compile, "check": cmd_check}
    if len(argv) != 2 or argv[1] not in commands:
        print(__doc__)
        return 2
    return commands[argv[1]]() or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
