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
import contextlib
import time
from urllib.parse import urlencode

from qgis.PyQt.QtCore import QObject, QThread, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices

from .. import config
from .. import net
from ..i18n import tr
from . import pkce
from .loopback import LoopbackReceiver
from .request_signer import TokenSnapshot
from . import token_store

#: Refresh this many seconds before the token actually expires, so a request
#: that starts valid does not finish expired.
_EXPIRY_MARGIN_SEC = 60
#: The service layers' signer is kept fresh this long before the token expires
#: (TCK-2348): QGIS fetches on its own schedule, and a refresh must come first.
_SIGNER_MARGIN_SEC = 300
#: A session confirmed by ALIGN this recently is not asked again when a
#: project opens (TCK-2348, CA-23).
_VERIFIED_FOR_SEC = 300

#: Why the session ended, for the words the plugin uses (TCK-2348, CA-21):
#: the user signed out, ALIGN ended it (a revocation in Settings →
#: Integrations), or it expired.
ENDED_BY_USER = "user"
ENDED_BY_ALIGN = "ended"
EXPIRED = "expired"


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

        if result is not None and result.get("error") == "cancelled":
            # Abandoned for a fresh attempt (TCK-2348, CA-25): nothing to say.
            self.finished.emit(False, "")
            return
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
    #: A sign-in started, or ended in any way (TCK-2348, CA-25): the panel
    #: shows the wait, its Cancel and Copy link, while :attr:`pending_url` is set.
    pending_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._worker = None
        self._receiver = None
        self._finished_slot = None
        #: The consent address of the sign-in waiting in the browser, for
        #: "Copy link" (another browser can finish it); ``None`` otherwise.
        self.pending_url = None
        #: Why the last session ended: ENDED_BY_USER, ENDED_BY_ALIGN or EXPIRED.
        self.end_reason = None
        self._verified_at = 0.0
        #: What the service layers' signer reads (TCK-2348): written here, on
        #: the interface thread, every time the session's token changes.
        self.snapshot = TokenSnapshot()

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
        """Start the loopback flow. Returns immediately; watch the signals.

        A sign-in still waiting in a browser is abandoned for a fresh one
        (TCK-2348, CA-25): closing the browser, or choosing another, must
        never leave the user waiting five minutes.
        """
        if self._thread is not None:
            self.cancel_sign_in()

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
        url = "{}?{}".format(config.authorize_url(), query)
        opened = QDesktopServices.openUrl(QUrl(url))
        if not opened:
            receiver.close()
            self.failed.emit(tr("Could not open your browser to sign in to ALIGN."))
            return

        self._receiver = receiver
        self.pending_url = url
        self._worker = _SignInWorker(receiver, verifier, receiver.redirect_uri)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # Bound to this attempt: a result an abandoned attempt still delivers
        # is recognised and dropped, never mistaken for the new one's.
        worker = self._worker
        self._finished_slot = lambda success, message: self._on_finished(worker, success, message)
        self._worker.finished.connect(self._finished_slot)
        self._thread.start()

        self.waiting_for_browser.emit()
        self.pending_changed.emit()

    def cancel_sign_in(self):
        """Give up the sign-in waiting in the browser (CA-25)."""
        if self._thread is None:
            return
        self._abandon()
        self.pending_changed.emit()

    def _abandon(self):
        """Close the waiting attempt's port and forget it: its redirect, if it
        ever comes, finds nobody, and its code is never exchanged."""
        with contextlib.suppress(TypeError, RuntimeError):
            self._worker.finished.disconnect(self._finished_slot)
        self._receiver.cancel()
        self._thread.quit()
        self._thread.wait()
        self._thread = self._worker = self._receiver = None
        self.pending_url = None

    def _on_finished(self, worker, success, message):
        if worker is not self._worker:
            return
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self._worker = None
        self._receiver = None
        self.pending_url = None
        self.pending_changed.emit()
        if success:
            self.end_reason = None
            self._verified_at = time.time()
            self.prime_signer()
            self.signed_in.emit()
        elif message:
            self.failed.emit(message)

    # -- the service layers' signer (TCK-2348) --------------------------------

    def prime_signer(self):
        """Hand the stored token to the signer, without a network call.

        An expired token reads as none there; :meth:`keep_signer_fresh`
        refreshes it when a service layer needs it.
        """
        self.snapshot.clear()
        # A locked store signs nothing, and must not stop QGIS loading.
        with contextlib.suppress(Exception):
            if token_store.has_session():
                self.snapshot.set(token_store.access_token(), token_store.access_expires_at())

    def keep_signer_fresh(self):
        """Refresh the token before the signer runs out of it.

        Called on the interface thread, by a timer and when a project with
        service layers opens. A refresh ALIGN refuses ends the session.

        :returns: whether the signer now holds a live token
        """
        if not token_store.has_session():
            self.snapshot.clear()
            return False
        if self.snapshot.expires_at() - _SIGNER_MARGIN_SEC > time.time():
            return True
        return self.access_token(fresh_for=_SIGNER_MARGIN_SEC) is not None

    # -- using the session --------------------------------------------------

    def access_token(self, fresh_for=_EXPIRY_MARGIN_SEC):
        """A token good for the next call, refreshing it if needed.

        :param fresh_for: refresh unless the token lives this many seconds more
        :returns: the token, or ``None`` when the session is gone — in which
            case the caller asks the user to sign in again rather than retrying.
        """
        if not token_store.has_session():
            self.snapshot.clear()
            return None

        expires_at = token_store.access_expires_at()
        if expires_at - fresh_for > time.time():
            existing = token_store.access_token()
            if existing:
                self.snapshot.set(existing, expires_at)
                return existing

        response = net.post_json(
            config.token_endpoint(),
            {"grant_type": "refresh_token", "refresh_token": token_store.refresh_token()},
        )
        if not response.ok:
            if response.status == 400:
                # A refused refresh means the session is over: revoked in
                # Settings → Integrations, a rotated token replayed, or expired.
                # ALIGN says which (TCK-2348, CA-21). Holding on to it would
                # only produce 401s.
                expired = isinstance(response.body, dict) and response.body.get("align_session") == EXPIRED
                self._end(EXPIRED if expired else ENDED_BY_ALIGN)
            # Anything else (no network, ALIGN unreachable or failing) says
            # nothing about the session: it is kept, and asked again later.
            return None

        body = response.body
        expires_at = time.time() + float(body.get("expires_in", 3600))
        token_store.update_access_token(
            access_token=body["access_token"],
            access_expires_at=expires_at,
            refresh_token=body.get("refresh_token"),
            scopes=body.get("scope") if isinstance(body.get("scope"), str) else None,
        )
        self.snapshot.set(body["access_token"], expires_at)
        self._verified_at = time.time()
        return body["access_token"]

    def verify(self):
        """Ask ALIGN whether the session still stands, now (TCK-2348).

        A refresh, whatever the token's age: a revocation in Settings →
        Integrations leaves tokens valid by their date on this computer, and
        only ALIGN knows. Refused, the session ends (``signed_out``, with
        :attr:`end_reason`); unreachable, it is kept.

        :returns: True when ALIGN confirmed it, False when it ended or could not be asked
        """
        if not token_store.has_session():
            return False
        return self.access_token(fresh_for=float("inf")) is not None

    def verify_if_stale(self):
        """:meth:`verify`, unless ALIGN confirmed the session a moment ago (CA-23)."""
        if not token_store.has_session():
            return False
        if time.time() - self._verified_at < _VERIFIED_FOR_SEC:
            return True
        return self.verify()

    def _end(self, reason):
        """Forget the session locally and say why it ended."""
        token_store.clear()
        self.snapshot.clear()
        self._verified_at = 0.0
        self.end_reason = reason
        self.signed_out.emit()

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

        self._end(ENDED_BY_USER)
        if not confirmed:
            self.failed.emit(
                tr(
                    "Signed out on this computer, but ALIGN did not confirm it: {} "
                    "Revoke the device from Settings → Integrations to be sure."
                ).format(reason or tr("no answer from the server."))
            )
        return confirmed, reason
