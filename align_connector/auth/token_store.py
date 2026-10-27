# -*- coding: utf-8 -*-
"""Where the tokens live: QGIS' own encrypted authentication database.

QGIS ships a credential store — an SQLite database whose secret values are
encrypted with a master password the user sets once. Using it means the plugin
never invents storage, never writes a token to a settings file, and never puts
one in a ``.qgs`` project that gets shared.

**Why ``storeAuthSetting`` and not an authentication configuration.** An
*authentication configuration* (the ``authcfg`` id) exists so that a **layer**
can carry credentials — QGIS injects them when it fetches the layer. Nothing
here is a layer: the plugin itself calls the API. ``storeAuthSetting`` is the
same database's API for exactly this — an arbitrary secret, encrypted at rest —
without pretending to be an authentication method we do not use.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.core import QgsApplication

from ..i18n import tr

_PREFIX = "align-connector/"

_REFRESH_TOKEN = _PREFIX + "refresh_token"
_ACCESS_TOKEN = _PREFIX + "access_token"
_ACCESS_EXPIRY = _PREFIX + "access_expires_at"
_ACCOUNT = _PREFIX + "account"
#: The scopes ALIGN granted, as its token answer listed them (TCK-2320): a
#: session from before the feature services lacks theirs, and the panel says
#: so before a publish rather than after the upload.
_SCOPES = _PREFIX + "scopes"


class MasterPasswordRequired(Exception):
    """QGIS' credential store is locked and the user did not unlock it.

    Not an error to swallow: without the store there is nowhere safe to put a
    token, and the honest answer is to stop rather than fall back to plain text.
    """


def _manager():
    return QgsApplication.authManager()


def ensure_unlocked():
    """Make sure the credential store is usable, prompting once if needed.

    QGIS asks the user to create or enter the master password. If they decline,
    we stop — writing the token anywhere else would defeat the point.
    """
    manager = _manager()
    if manager.masterPasswordIsSet():
        return
    # verify=True lets QGIS prompt and validate; it returns False if the user
    # cancels or gets it wrong.
    if not manager.setMasterPassword(True):
        raise MasterPasswordRequired(
            tr("QGIS' password manager must be unlocked to store your ALIGN session securely.")
        )


def _write(key, value):
    if not _manager().storeAuthSetting(key, value, True):
        raise RuntimeError("could not write {} to the QGIS credential store".format(key))


def _read(key):
    value = _manager().authSetting(key, "", True)
    return value or None


def save_session(refresh_token, access_token, access_expires_at, account=None, scopes=None):
    """Persist a session. Values are encrypted at rest by QGIS."""
    ensure_unlocked()
    _write(_REFRESH_TOKEN, refresh_token)
    _write(_ACCESS_TOKEN, access_token)
    _write(_ACCESS_EXPIRY, str(access_expires_at))
    if account:
        _write(_ACCOUNT, account)
    if scopes:
        _write(_SCOPES, scopes)


def update_access_token(access_token, access_expires_at, refresh_token=None, scopes=None):
    """Record the outcome of a refresh.

    The refresh token rotates on every use: storing the new one is not an
    optimisation, it is the only way the next refresh can work.
    """
    ensure_unlocked()
    _write(_ACCESS_TOKEN, access_token)
    _write(_ACCESS_EXPIRY, str(access_expires_at))
    if refresh_token:
        _write(_REFRESH_TOKEN, refresh_token)
    if scopes:
        _write(_SCOPES, scopes)


def refresh_token():
    return _read(_REFRESH_TOKEN)


def access_token():
    return _read(_ACCESS_TOKEN)


def access_expires_at():
    raw = _read(_ACCESS_EXPIRY)
    try:
        return float(raw) if raw else 0.0
    except (TypeError, ValueError):
        return 0.0


def account():
    """The signed-in identity, shown in the UI. Not a secret, stored alongside."""
    return _read(_ACCOUNT)


def granted_scopes():
    """The scopes of the session, empty when ALIGN never listed them."""
    try:
        return tuple((_read(_SCOPES) or "").split())
    except Exception:
        # A locked store grants nothing, and asking must never raise here:
        # this is called to decide what the panel offers.
        return ()


def clear():
    """Forget the session locally. Idempotent.

    Signing out also revokes server-side; this is the local half, and it must
    run even if the network call failed — a token we cannot revoke is a token
    we should at least stop holding.
    """
    manager = _manager()
    for key in (_REFRESH_TOKEN, _ACCESS_TOKEN, _ACCESS_EXPIRY, _ACCOUNT, _SCOPES):
        manager.removeAuthSetting(key)


def has_session():
    """True when a refresh token is on file — i.e. sign-in can be resumed."""
    try:
        return bool(refresh_token())
    except Exception:
        # A locked store is not a session, and asking must never raise here:
        # this is called to decide what the menu shows.
        return False
