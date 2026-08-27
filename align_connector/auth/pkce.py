# -*- coding: utf-8 -*-
"""PKCE and the anti-forgery state (RFC 7636, RFC 6749 §10.12).

A desktop client holds no secret — anyone can read the plugin's source. What
proves that the code being exchanged belongs to the client that asked for it is
the pair generated here: the challenge travels through the browser, the
verifier never leaves the process.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import base64
import hashlib
import secrets


def _b64url(raw):
    """base64url without padding — the only encoding RFC 7636 accepts."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def new_verifier():
    """A high-entropy verifier (43 characters, 256 bits)."""
    return _b64url(secrets.token_bytes(32))


def challenge_for(verifier):
    """The S256 challenge derived from a verifier.

    Only the challenge is sent through the browser. The server stores it and,
    at exchange time, recomputes it from the verifier we present: a code
    intercepted in the browser is useless without the verifier.
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url(digest)


def new_state():
    """Opaque value echoed back by the server, compared on return.

    Guards the loopback listener against a request it did not initiate: any
    local process can reach 127.0.0.1, so receiving *a* code is not proof of
    receiving *our* code.
    """
    return _b64url(secrets.token_bytes(16))
