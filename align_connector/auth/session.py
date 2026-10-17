# -*- coding: utf-8 -*-
"""The sign-in flow, and the session it produces.

One object owns the whole lifecycle — sign in, hand out a valid access token,
sign out — so that no other module ever has to think about token expiry.

The flow, end to end:

1. Generate a PKCE pair and an anti-forgery ``state``.
2. Open a one-shot loopback listener on ``127.0.0.1``.
3. Open the system browser on ALIGN's consent page.
4. The user approves in ALIGN; the browser is redirected to the listener.
5. Exchange the code — with the verifier — for a token pair.

Steps 4 and 5 run on a worker thread: the wait is human-scale (up to five
minutes) and QGIS must stay usable throughout.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import time
from urllib.parse import urlencode

from qgis.PyQt.QtCore import QObject, QThread, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices

from .. import config
from .. import net
from ..i18n import tr
from . import pkce
from .loopback import LoopbackReceiver
from . import token_store

#: Refresh this many seconds before the token actually expires, so a request
#: that starts valid does not finish expired.
_EXPIRY_MARGIN_SEC = 60


class _SignInWorker(QObject):
    """Waits for the redirect, then exchanges the code. Runs off the UI thread."""

    finished = pyqtSignal(bool, str)  # success, message-or-account

    def __init__(self, receiver, verifier, redirect_uri):
        super().__init__()
        self._receiver = receiver
        self._verifier = verifier
        self._redirect_uri = redirect_uri

    def run(self):
        try:
            result = self._receiver.wait()
        finally:
            self._receiver.close()

        if result is None:
            self.finished.emit(False, tr("Timed out waiting for the browser. Nothing was granted."))
            return
        if "error" in result:
            if result["error"] == "access_denied":
                self.finished.emit(False, tr("Authorization was refused in the browser."))
            else:
                self.finished.emit(False, tr("Authorization failed ({}).").format(result["error"]))
            return

        response = net.post_json(
            config.token_endpoint(),
            {
                "grant_type": "authorization_code",
                "code": result["code"],
                "code_verifier": self._verifier,
                "redirect_uri": self._redirect_uri,
                "client_id": config.CLIENT_ID,
            },
        )
        if not response.ok:
            self.finished.emit(False, response.message(tr("Could not complete sign-in")))
            return

        body = response.body
        try:
            token_store.save_session(
                refresh_token=body["refresh_token"],
                access_token=body["access_token"],
                access_expires_at=time.time() + float(body.get("expires_in", 3600)),
                scopes=body.get("scope") if isinstance(body.get("scope"), str) else None,
            )
        except Exception as exc:  # noqa: BLE001 — surfaced to the user, never swallowed
            self.finished.emit(False, str(exc))
            return

        self.finished.emit(True, "")


class AlignSession(QObject):
    """The plugin's single view of "am I connected, and as whom?"."""

    signed_in = pyqtSignal()
    signed_out = pyqtSignal()
    failed = pyqtSignal(str)
    #: Emitted once the browser has been opened, so the UI can say what to do.
    waiting_for_browser = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._worker = None

    # -- state --------------------------------------------------------------

    @staticmethod
    def is_signed_in():
        return token_store.has_session()

    @staticmethod
    def account():
        return token_store.account()

    @staticmethod
    def has_scope(scope):
        """Whether ALIGN granted this session `scope`; a session that never
        said which scopes it holds is from before the scope asked."""
        return scope in token_store.granted_scopes()

    # -- sign in ------------------------------------------------------------

    def sign_in(self):
        """Start the loopback flow. Returns immediately; watch the signals."""
        if self._thread is not None:
            self.failed.emit(tr("A sign-in is already in progress."))
            return

        try:
            token_store.ensure_unlocked()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return

        verifier = pkce.new_verifier()
        state = pkce.new_state()
        receiver = LoopbackReceiver(expected_state=state)
        receiver.start()

        query = urlencode(
            {
                "client_id": config.CLIENT_ID,
                "redirect_uri": receiver.redirect_uri,
                "code_challenge": pkce.challenge_for(verifier),
                "code_challenge_method": "S256",
                "scope": config.scope_string(),
                "state": state,
            }
        )
        opened = QDesktopServices.openUrl(QUrl("{}?{}".format(config.authorize_url(), query)))
        if not opened:
            receiver.close()
            self.failed.emit(tr("Could not open your browser to sign in to ALIGN."))
            return

        self._worker = _SignInWorker(receiver, verifier, receiver.redirect_uri)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

        self.waiting_for_browser.emit()

    def _on_finished(self, success, message):
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self._worker = None
        if success:
            self.signed_in.emit()
        else:
            self.failed.emit(message)

    # -- using the session --------------------------------------------------

    def access_token(self):
        """A token good for the next call, refreshing it if needed.

        :returns: the token, or ``None`` when the session is gone — in which
            case the caller asks the user to sign in again rather than retrying.
        """
        if not token_store.has_session():
            return None

        if token_store.access_expires_at() - _EXPIRY_MARGIN_SEC > time.time():
            existing = token_store.access_token()
            if existing:
                return existing

        response = net.post_json(
            config.token_endpoint(),
            {"grant_type": "refresh_token", "refresh_token": token_store.refresh_token()},
        )
        if not response.ok:
            # A refused refresh means the session is over — most often because
            # it was revoked, or because a rotated token was replayed. Holding
            # on to it would only produce 401s.
            token_store.clear()
            self.signed_out.emit()
            return None

        body = response.body
        token_store.update_access_token(
            access_token=body["access_token"],
            access_expires_at=time.time() + float(body.get("expires_in", 3600)),
            refresh_token=body.get("refresh_token"),
            scopes=body.get("scope") if isinstance(body.get("scope"), str) else None,
        )
        return body["access_token"]

    # -- sign out -----------------------------------------------------------

    def sign_out(self):
        """Revoke server-side, then forget locally.

        Two things must both happen, and only one of them is ours to guarantee:

        * **Server-side revocation** ends the session for real. If it does not
          happen, the device stays listed in ALIGN and its refresh token lives
          on — the user believes they disconnected, and they did not. So the
          outcome is checked and reported, never assumed. Signing out and back
          in used to leave a ghost device behind for exactly this reason.
        * **Local erasure** runs regardless: a token we cannot revoke is a token
          we should at least stop holding.

        Both the access and the refresh token are presented. The server ends the
        whole session from either, but a session whose refresh token has just
        rotated would otherwise be revoked from a value we no longer hold.

        :returns: ``(True, None)`` when the server confirmed, ``(False, reason)``
            when the local half is done but the session may still stand.
        """
        refresh = token_store.refresh_token()
        access = token_store.access_token()

        confirmed = False
        reason = None
        for token in (refresh, access):
            if not token:
                continue
            result = net.post_json(config.revoke_endpoint(), {"token": token})
            if result.ok:
                confirmed = True
                break
            reason = result.message(tr("ALIGN did not confirm the disconnection"))

        if not refresh and not access:
            # Nothing to revoke: the local store was already empty.
            confirmed = True

        token_store.clear()
        self.signed_out.emit()
        if not confirmed:
            self.failed.emit(
                tr(
                    "Signed out on this computer, but ALIGN did not confirm it: {} "
                    "Revoke the device from Settings → Integrations to be sure."
                ).format(reason or tr("no answer from the server."))
            )
        return confirmed, reason
