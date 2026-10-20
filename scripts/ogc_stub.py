# -*- coding: utf-8 -*-
"""A tiny OGC API Features server, for the smoke scripts that run inside QGIS.

It stands for ALIGN's facade: one service, two layers of three points (the
second for a service of several layers, TCK-2346), every answer ``private,
no-store`` as ALIGN's facade sends (TCK-2348). It records
who sent what, so a script can tell which requests the plugin signed; with
``require_token`` it answers 401 to an unsigned request, as ALIGN does.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE = "/api/v1/orgs/org-1/feature-services/svc-jardins/ogc"
LAYER = "0123456789abcdef"
LAYER2 = "fedcba9876543210"
LAYERS = (LAYER, LAYER2)
TOKEN = "alg_at_smoke"


class Recorder(BaseHTTPRequestHandler):
    """Answers a tiny OGC API Features, and records who sent what."""

    seen = None  # [(port, path, authorization)]
    other_port = None
    require_token = False
    #: The token the service accepts, when it requires one (a revocation changes it).
    token = TOKEN
    #: What ALIGN answers a write of a feature (new or changed), when not a success: (status, body), e.g. a 422 for its rules.
    items_answer = None
    #: What ALIGN's token endpoint answers a refresh: (status, body).
    refresh_answer = (400, {"error": "invalid_grant", "error_description": "Unknown refresh token", "align_session": "ended"})

    def log_message(self, *_args):
        pass

    def _send(self, status, body=None, content_type="application/json", headers=None):
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "private, no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):  # noqa: N802 — http.server's name
        """Part 4 discovery, as ALIGN answers it: what the caller may do on the items."""
        Recorder.seen.append((self.server.server_address[1], "OPTIONS " + self.path, self.headers.get("Authorization")))
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, OPTIONS, POST, PUT, PATCH, DELETE")
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()

    def do_GET(self):  # noqa: N802 — http.server's name
        auth = self.headers.get("Authorization")
        Recorder.seen.append((self.server.server_address[1], self.path, auth))
        base = "http://127.0.0.1:{}{}".format(self.server.server_address[1], SERVICE)
        path = self.path.split("?")[0]
        if path.endswith("/redirect-out"):
            return self._send(302, headers={"Location": "http://127.0.0.1:{}{}/collections".format(Recorder.other_port, SERVICE)})
        if Recorder.require_token and path.startswith(SERVICE) and auth != "Bearer " + Recorder.token:
            return self._send(401, {"code": "UNAUTHORIZED"})
        if path in (SERVICE, SERVICE + "/"):
            return self._send(
                200,
                {
                    "links": [
                        {"href": base + "/api", "rel": "service-desc", "type": "application/vnd.oai.openapi+json;version=3.0"},
                        {"href": base + "/conformance", "rel": "conformance", "type": "application/json"},
                        {"href": base + "/collections", "rel": "data", "type": "application/json"},
                    ]
                },
            )
        if path == SERVICE + "/api":
            return self._send(200, {"openapi": "3.0.2", "info": {"title": "ALIGN", "version": "1"}, "paths": {}}, "application/vnd.oai.openapi+json;version=3.0")
        if path == SERVICE + "/conformance":
            return self._send(
                200,
                {
                    "conformsTo": [
                        "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
                        "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
                        "http://www.opengis.net/spec/ogcapi-features-4/1.0/conf/create-replace-delete",
                        "http://www.opengis.net/spec/ogcapi-features-4/1.0/conf/update",
                        "http://www.opengis.net/spec/ogcapi-features-4/1.0/conf/features",
                    ]
                },
            )
        collections = {
            layer_id: {
                "id": layer_id,
                "title": "Jardins",
                "itemType": "feature",
                "extent": {"spatial": {"bbox": [[2.2, 48.8, 2.5, 48.9]]}},
                "links": [{"href": base + "/collections/" + layer_id + "/items", "rel": "items", "type": "application/geo+json"}],
            }
            for layer_id in LAYERS
        }
        if path == SERVICE + "/collections":
            return self._send(200, {"collections": list(collections.values()), "links": []})
        named = path[len(SERVICE + "/collections/"):].split("/")[0] if path.startswith(SERVICE + "/collections/") else None
        if named in collections and path == SERVICE + "/collections/" + named:
            return self._send(200, collections[named])
        if named in collections and path == SERVICE + "/collections/" + named + "/items":
            features = [
                {"type": "Feature", "id": i, "geometry": {"type": "Point", "coordinates": [2.3 + i / 100.0, 48.85]}, "properties": {"nom": "Jardin {}".format(i)}}
                for i in (1, 2, 3)
            ]
            return self._send(200, {"type": "FeatureCollection", "features": features, "numberMatched": 3, "numberReturned": 3, "links": []}, "application/geo+json")
        return self._send(404, {"code": "NOT_FOUND"})


    def do_POST(self):  # noqa: N802 — http.server's name
        """ALIGN's token endpoint, and the items of the service (part 4)."""
        auth = self.headers.get("Authorization")
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        Recorder.seen.append((self.server.server_address[1], "POST " + self.path, auth))
        path = self.path.split("?")[0]
        if path == "/api/v1/oauth/token":
            status, body = Recorder.refresh_answer
            return self._send(status, body)
        if Recorder.require_token and path.startswith(SERVICE) and auth != "Bearer " + Recorder.token:
            return self._send(401, {"code": "UNAUTHORIZED"})
        if path == SERVICE + "/collections/" + LAYER + "/items" and Recorder.items_answer is not None:
            return self._send(*Recorder.items_answer)
        if path == SERVICE + "/collections/" + LAYER + "/items":
            base = "http://127.0.0.1:{}{}".format(self.server.server_address[1], SERVICE)
            return self._send(201, {}, headers={"Location": base + "/collections/" + LAYER + "/items/9"})
        return self._send(404, {"code": "NOT_FOUND"})


    def _change(self, method):
        """A feature changed (part 4): PUT or PATCH, 204, or ``items_answer``."""
        auth = self.headers.get("Authorization")
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        Recorder.seen.append((self.server.server_address[1], method + " " + self.path, auth))
        path = self.path.split("?")[0]
        if Recorder.require_token and path.startswith(SERVICE) and auth != "Bearer " + Recorder.token:
            return self._send(401, {"code": "UNAUTHORIZED"})
        if path.startswith(SERVICE + "/collections/" + LAYER + "/items/"):
            if Recorder.items_answer is not None:
                return self._send(*Recorder.items_answer)
            return self._send(204)
        return self._send(404, {"code": "NOT_FOUND"})

    def do_PUT(self):  # noqa: N802 — http.server's name
        return self._change("PUT")

    def do_PATCH(self):  # noqa: N802 — http.server's name
        return self._change("PATCH")


def _serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def serve():
    """Start a server on a free local port; the caller shuts it down."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
