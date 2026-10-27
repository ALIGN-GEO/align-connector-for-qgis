# -*- coding: utf-8 -*-
"""Asking ALIGN for the feature services of a lot, off the interface thread (TCK-2346).

The layers' files are already on ALIGN, each sent by its own
``PublishLayerTask``. This task does the second step for all of them: it
previews each file, asks for every service in **one** request (the checked
layers as one service, or one service per layer), then follows each job to
its end. One request, whatever the number of layers: ALIGN checks it whole
before it starts anything, and a lot is never cut by the limit on how many
publications start per minute.

What the task never decides is whether the user may publish: ALIGN does, and
this reports what it answered.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import time

from qgis.core import QgsTask

from .. import api
from ..i18n import tr
from ..qtcompat import qt_enum
from .feature_service import (
    PENDING,
    ServicePublishFailed,
    follow_jobs,
    plan_services,
    published_service,
)
from .task import with_capacity

#: Where the previews end and the services' preparation begins in the progress bar.
_PREVIEWED = 10.0

#: Seconds between two polls of a feature-service job.
_POLL_SECONDS = 3


def services_title(entries, service_title):
    """The task's title in QGIS's task list and in the Activity view."""
    if service_title is not None:
        return tr("Feature service “{}”").format(service_title)
    if len(entries) == 1:
        return tr("Feature service “{}”").format(entries[0][2])
    return tr("{} feature services").format(len(entries))


class PublishServicesTask(QgsTask):
    """Preview the sent files, ask ALIGN for their services, follow each job."""

    def __init__(self, session, org_id, org_name, entries, service_title=None, visibility=None, capacity_line=None):
        """
        :param entries: ``[(local_layer_id, asset_id, title)]``: each sent
            layer, its file on ALIGN, and its name, in the panel's order
        :param service_title: the name of the one service the layers make;
            ``None``: one service per layer, under the layer's name
        """
        super().__init__(services_title(entries, service_title), qt_enum(QgsTask, "Flag", "CanCancel"))
        self.session = session
        self.org_id = org_id
        self.org_name = org_name
        self.entries = list(entries)
        self.service_title = service_title
        self.visibility = visibility
        self.capacity_line = capacity_line
        #: The row's title in the Activity view.
        self.asset_name = services_title(self.entries, service_title)

        #: Set on completion, read by whoever connected to `taskCompleted`:
        #: one ``feature_service.PublishedService`` per service, in the order
        #: of the request, then the layers ALIGN refused before it.
        self.services = []
        self.error_message = None
        self.error_status = None
        self.error_code = None
        self.needs_sign_in = False
        self.was_cancelled = False
        #: The follow stopped on an error (a session that expired): the
        #: services not yet followed are still prepared by ALIGN.
        self.follow_error = None

    # -- worker thread ------------------------------------------------------

    def run(self):
        try:
            requested, refused = plan_services(self.entries, self._preview, self.service_title)
            self.setProgress(_PREVIEWED)
            if not requested:
                self.services = refused
                return True
            if self.isCanceled():
                self.was_cancelled = True
                return False
            jobs = api.start_feature_services(self.session, self.org_id, requested, visibility=self.visibility)
            self.services = self._follow(requested, jobs) + refused
            self.setProgress(100)
            return True
        except ServicePublishFailed as exc:
            self.error_message = self._failure(str(exc))
            return False
        except api.ApiError as exc:
            self.error_message = self._failure(with_capacity(exc.message, exc.status, self.capacity_line))
            self.error_status = exc.status
            self.error_code = exc.code
            self.needs_sign_in = exc.needs_sign_in
            return False

    def finished(self, result):
        """Runs on the interface thread. Kept empty on purpose, as for
        ``PublishLayerTask``: the plugin reads the attributes above."""
        return

    # -- internals ----------------------------------------------------------

    def _preview(self, asset_id):
        """ALIGN's preview of a file; a refusal about the layer itself is the
        layer's, never the whole lot's (``plan_services`` decides)."""
        try:
            return api.preview_feature_service(self.session, self.org_id, asset_id)
        except api.ApiError as exc:
            if exc.code == "FEATURE_PUBLICATION_REFUSED":
                raise ServicePublishFailed(exc.message) from exc
            raise

    def _follow(self, requested, jobs):
        local_of = {asset_id: local_layer_id for local_layer_id, asset_id, _title in self.entries}
        followed = []
        try:
            for state, job, reason in follow_jobs(
                jobs,
                lambda job_id: api.feature_service_job(self.session, self.org_id, job_id),
                wait=lambda: time.sleep(_POLL_SECONDS),
                cancelled=self.isCanceled,
                on_progress=lambda share: self.setProgress(_PREVIEWED + share * (99.0 - _PREVIEWED)),
            ):
                followed.append((state, job, reason))
        except api.ApiError as exc:
            # The follow stopped, not the jobs: the services left will appear.
            self.follow_error = exc.message
            self.needs_sign_in = exc.needs_sign_in
            followed.extend((PENDING, job, None) for job in jobs[len(followed):])
        return [
            published_service(asked["title"], state, job, reason, local_of)
            for asked, (state, job, reason) in zip(requested, followed)
        ]

    def _failure(self, message):
        """Nothing was published: the files stay in the Datastore, and the message says so."""
        if self.service_title is not None:
            return tr("The layers are stored in your Datastore, but they did not become the feature service “{}”: {}").format(
                self.service_title, message
            )
        if len(self.entries) == 1:
            return tr("“{}” is stored in your Datastore, but it did not become a feature service: {}").format(
                self.entries[0][2], message
            )
        return tr("The layers are stored in your Datastore, but they did not become feature services: {}").format(message)
