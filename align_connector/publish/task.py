# -*- coding: utf-8 -*-
"""Publishing a layer, off the interface thread.

A publish is export, then transfer, then catalogue. The transfer is the long
part — minutes for a modest layer, hours for a raster — so the whole thing runs
in a ``QgsTask``: QGIS keeps its progress bar, its cancel button, and its
responsiveness, and the user keeps working on the map meanwhile.

What the task never does is decide whether the user is allowed to publish.
ALIGN decides; this reports what it answered.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import os
import shutil

from qgis.core import QgsTask

from .. import api
from ..metadata import plugin_version
from .exporter import ExportFailed, export_layer
from .uploader import ResumableUploader, UploadCancelled, UploadFailed

#: How the export is weighted against the transfer in the progress bar. Writing
#: the file is quick; sending it is what takes the time.
_EXPORT_SHARE = 5.0


class PublishLayerTask(QgsTask):
    """Export one layer, send it, and catalogue it in ALIGN."""

    def __init__(self, session, layer, org_id, org_name, asset_name, selected_only=False):
        super().__init__("Publishing “{}” to ALIGN".format(asset_name), QgsTask.CanCancel)
        self.session = session
        self.layer = layer
        self.org_id = org_id
        self.org_name = org_name
        self.asset_name = asset_name
        self.selected_only = selected_only
        self.layer_id = layer.id()

        #: Set on completion, read by whoever connected to `taskCompleted`.
        self.error_message = None
        self.needs_sign_in = False
        self.was_cancelled = False
        self.asset_id = None

        self._workdir = None

    # -- worker thread ------------------------------------------------------

    def run(self):
        try:
            path, filename, content_type = export_layer(
                self.layer, selected_only=self.selected_only
            )
            self._workdir = os.path.dirname(path)
            self.setProgress(_EXPORT_SHARE)

            size_bytes = os.path.getsize(path)
            session_info = api.open_upload_session(
                self.session, self.org_id, filename, content_type, size_bytes
            )
            self.asset_id = session_info["assetId"]

            uploader = ResumableUploader(
                path,
                session_info["sessionUrl"],
                session_info["chunkSizeBytes"],
            )
            uploader.upload(
                on_progress=self._on_upload_progress,
                is_cancelled=self.isCanceled,
            )

            api.finalize(
                self.session,
                self.org_id,
                self.asset_id,
                filename,
                self.asset_name,
                provenance=self._provenance(),
            )
            self.setProgress(100)
            return True

        except UploadCancelled:
            self.was_cancelled = True
            return False
        except (ExportFailed, UploadFailed) as exc:
            self.error_message = str(exc)
            return False
        except api.ApiError as exc:
            self.error_message = exc.message
            self.needs_sign_in = exc.needs_sign_in
            return False
        finally:
            self._cleanup()

    def finished(self, result):
        """Runs on the interface thread. Kept empty on purpose.

        The plugin connects to `taskCompleted`/`taskTerminated` and reads the
        attributes above: putting UI here would tie this class to one caller.
        """
        return

    # -- internals ----------------------------------------------------------

    def _on_upload_progress(self, sent, total):
        if total <= 0:
            return
        self.setProgress(_EXPORT_SHARE + (sent / float(total)) * (100.0 - _EXPORT_SHARE))

    def _provenance(self):
        """What ALIGN records about where this asset came from.

        Enough to recognise later that *this* layer, from *this* client, has
        already been published — and nothing more (data minimisation).
        """
        from qgis.core import Qgis

        return {
            "source": "qgis-plugin",
            "sourceLayerId": self.layer_id,
            "clientVersion": plugin_version(),
            "appVersion": Qgis.QGIS_VERSION.split("-")[0],
            "publishedAt": _utc_now(),
        }

    def _cleanup(self):
        """Remove the exported copy. It was never the user's file."""
        if self._workdir and os.path.isdir(self._workdir):
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None


def _utc_now():
    """An ISO-8601 UTC timestamp, the shape the API validates against."""
    from datetime import datetime

    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
