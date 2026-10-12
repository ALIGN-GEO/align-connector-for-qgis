# -*- coding: utf-8 -*-
"""The diagnostic a user pastes in a bug report (TCK-2219, ADR-0058 V1-g).

A report that says "publishing fails" costs a round trip for the version of
QGIS, the server the plugin talks to, and the exact refusal. One click puts
all of that in the clipboard, in English whatever the plugin's language
(it is read by the support, not by the user), and nothing leaves the machine
unless the user pastes it somewhere.

What it holds: versions (plugin, QGIS, Qt, PyQt, Python, system), the
plugin's language, the two addresses (and whether they are the defaults),
whether a session is open, the names of the organizations the panel loaded,
the last publications with their outcome and ALIGN's refusal code, and the
layers the panel refuses with their reason.

What it never holds: a token, an e-mail address, an account identifier. The
composition reads none of them, and :func:`scrub` masks anything shaped like
one that would slip in through a message, as a second lock on the same door
(rule 23, secret out of artifact; minimisation).

Pure where it can be: :func:`compose` takes plain values and is proven by
``scripts/smoke_diagnostic.py`` without QGIS; :func:`environment` is the
only function that asks QGIS and Qt.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import platform
import re
import time

from . import config, metadata

#: How many publications the Activity view keeps for the diagnostic.
MAX_HISTORY = 10

#: What must never appear, whatever path it took: our tokens (``alg_at_`` /
#: ``alg_rt_``), a bearer header, an e-mail address.
_FORBIDDEN = (
    re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.]+", re.IGNORECASE),
    re.compile(r"alg_(?:at|rt)_[A-Za-z0-9_\-\.]+"),
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
)
REDACTED = "[redacted]"


def environment():
    """Versions and addresses, read from QGIS and Qt. Each value degrades to
    ``unknown`` on its own: a diagnostic that fails to compose is the one
    report that would never be filed."""
    env = {
        "plugin": "{} {}".format(metadata.plugin_name(), metadata.plugin_version()),
        "language": _language(),
        "qgis": _read(lambda: __import__("qgis.core", fromlist=["Qgis"]).Qgis.QGIS_VERSION),
        "qt": _read(lambda: __import__("qgis.PyQt.QtCore", fromlist=["QT_VERSION_STR"]).QT_VERSION_STR),
        "pyqt": _read(lambda: __import__("qgis.PyQt.QtCore", fromlist=["PYQT_VERSION_STR"]).PYQT_VERSION_STR),
        "python": _read(platform.python_version),
        "system": _read(platform.platform),
        "app_url": config.app_url(),
        "app_custom": config.app_url() != config.DEFAULT_APP_URL,
        "api_url": config.api_url(),
        "api_custom": config.api_url() != config.DEFAULT_API_URL,
    }
    return env


def compose(env, signed_in, organizations, history, blocked, now=None):
    """The text for the clipboard.

    :param env: what :func:`environment` returns
    :param signed_in: whether a session is open
    :param organizations: ``[{"name", "role"}]`` as the panel loaded them, or
        ``None`` when the panel never did
    :param history: the Activity view's last entries, newest first:
        ``{"title", "org", "level", "text", "status", "code"}``
    :param blocked: ``[(layer name, reason)]`` from the Messages view
    :param now: epoch seconds, for the header (the clock by default)
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now if now is not None else time.time()))
    lines = [
        "ALIGN Connector diagnostic",
        "Generated {}. Versions, server addresses, session state, organization names, the last "
        "publications with ALIGN's refusal codes, and the layers the panel refuses. "
        "No token, no e-mail address, no account identifier.".format(stamp),
        "",
        "Plugin: {} (language: {})".format(env.get("plugin", "unknown"), env.get("language", "unknown")),
        "QGIS: {} | Qt {} | PyQt {} | Python {}".format(
            env.get("qgis", "unknown"), env.get("qt", "unknown"), env.get("pyqt", "unknown"), env.get("python", "unknown")
        ),
        "System: {}".format(env.get("system", "unknown")),
        "Application: {} ({})".format(env.get("app_url", "unknown"), "custom" if env.get("app_custom") else "default"),
        "API: {} ({})".format(env.get("api_url", "unknown"), "custom" if env.get("api_custom") else "default"),
        "Session: {}".format("signed in" if signed_in else "signed out"),
    ]
    if organizations is None:
        lines.append("Organizations: not loaded (the panel was not opened)")
    elif not organizations:
        lines.append("Organizations: none")
    else:
        lines.append("Organizations: " + ", ".join(_org_label(o) for o in organizations))

    lines.append("")
    lines.append("Last publications (newest first):")
    entries = list(history or [])[:MAX_HISTORY]
    if not entries:
        lines.append("- none in this session")
    for entry in entries:
        lines.append(_history_line(entry))

    lines.append("")
    lines.append("Layers the panel refuses:")
    if not blocked:
        lines.append("- none")
    for name, reason in blocked or []:
        lines.append("- {}: {}".format(name, reason or "no reason given"))

    return scrub("\n".join(lines)) + "\n"


def scrub(text):
    """Mask anything shaped like a token, a bearer header or an e-mail address."""
    for pattern in _FORBIDDEN:
        text = pattern.sub(REDACTED, text)
    return text


def _history_line(entry):
    get = entry.get if isinstance(entry, dict) else lambda key, default=None: getattr(entry, key, default)
    title = get("title") or "?"
    org = get("org") or "?"
    level = str(get("level") or "").upper() or "?"
    text = get("text") or ""
    code = get("code")
    status = get("status")
    tail = ""
    if status or code:
        tail = " [{}]".format(" ".join(str(x) for x in (status, code) if x))
    return "- {} -> {}: {}: {}{}".format(title, org, level, text, tail)


def _org_label(org):
    get = org.get if isinstance(org, dict) else lambda key, default=None: getattr(org, key, default)
    name = get("name") or "?"
    role = get("role")
    return "{} ({})".format(name, role) if role else name


def _language():
    try:
        from .i18n import language

        return language()
    except Exception:  # noqa: BLE001 — outside QGIS there is no locale to read
        return "unknown"


def _read(getter):
    try:
        value = getter()
    except Exception:  # noqa: BLE001 — one missing version must not sink the report
        return "unknown"
    return str(value) if value else "unknown"
