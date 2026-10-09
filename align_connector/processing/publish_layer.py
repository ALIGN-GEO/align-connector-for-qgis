# -*- coding: utf-8 -*-
"""``align:publishlayer`` — publish one layer to an organization's Datastore.

The same publication as the panel's, driven by parameters. Two phases,
because QGIS runs an algorithm on a worker thread and a QGIS layer is not
for other threads to read:

* :meth:`prepareAlgorithm`, on the interface thread, reads the layer: the
  verdict (can this layer be published at all?), the briefing (style,
  metadata, the title it proposes), the organization, and builds the
  publish task. Every refusal is raised here, before any transfer.
* :meth:`processAlgorithm`, on the worker thread, runs the task: export,
  resumable upload, finalize. Progress goes to the feedback, cancellation
  comes from it.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputString,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterMapLayer,
    QgsProcessingParameterString,
)

from .. import api, config
from ..i18n import tr
from ..panel.outcome import outcome_of
from ..publish import briefing, support
from ..publish.task import PublishLayerTask
from ..qtcompat import qt_enum

INPUT = "INPUT"
ORGANIZATION = "ORGANIZATION"
TITLE = "TITLE"
DESCRIPTION = "DESCRIPTION"
VISIBILITY = "VISIBILITY"
SELECTED_ONLY = "SELECTED_ONLY"
ASSET_ID = "ASSET_ID"
ALIGN_URL = "ALIGN_URL"
SERVABLE_ASSET_ID = "SERVABLE_ASSET_ID"

#: Visibility choices, in the order the enum parameter shows them.
VISIBILITIES = ("private", "org")


class PublishLayerAlgorithm(QgsProcessingAlgorithm):
    def __init__(self, session):
        super().__init__()
        self._session = session
        self._task = None
        self._org = None

    # -- identity -----------------------------------------------------------

    def createInstance(self):  # noqa: N802 — name imposed by the QGIS API
        return PublishLayerAlgorithm(self._session)

    def name(self):
        return "publishlayer"

    def displayName(self):  # noqa: N802 — name imposed by the QGIS API
        return tr("Publish layer to ALIGN")

    def group(self):
        return tr("Datastore")

    def groupId(self):  # noqa: N802 — name imposed by the QGIS API
        return "datastore"

    def shortHelpString(self):  # noqa: N802 — name imposed by the QGIS API
        return tr(
            "Publishes one vector or raster layer to an organization's Datastore on ALIGN, "
            "with its style and its metadata, exactly as the ALIGN panel does. "
            "Sign in to ALIGN first (Web menu, or the ALIGN toolbar button): the algorithm "
            "never opens a browser. The organization is given by its name or its id. "
            "A service layer (WMS, WFS, XYZ…) is not a file: register it as a reference from "
            "the ALIGN panel instead."
        )

    # -- parameters ---------------------------------------------------------

    def initAlgorithm(self, config=None):  # noqa: N802 — name imposed by the QGIS API
        self.addParameter(
            QgsProcessingParameterMapLayer(
                INPUT,
                tr("Layer"),
                types=[
                    qt_enum(QgsProcessing, "SourceType", "TypeVectorAnyGeometry"),
                    qt_enum(QgsProcessing, "SourceType", "TypeRaster"),
                ],
            )
        )
        self.addParameter(QgsProcessingParameterString(ORGANIZATION, tr("Organization (name or id)")))
        self.addParameter(QgsProcessingParameterString(TITLE, tr("Title on ALIGN (empty: the layer's)"), optional=True))
        self.addParameter(
            QgsProcessingParameterString(DESCRIPTION, tr("Description"), multiLine=True, optional=True)
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                VISIBILITY,
                tr("Visibility"),
                options=[tr("Private: only you"), tr("Organization: everyone in this organization")],
                defaultValue=0,
            )
        )
        self.addParameter(QgsProcessingParameterBoolean(SELECTED_ONLY, tr("Publish only the selected features"), defaultValue=False))
        self.addOutput(QgsProcessingOutputString(ASSET_ID, tr("Asset id")))
        self.addOutput(QgsProcessingOutputString(ALIGN_URL, tr("Item page on ALIGN")))
        self.addOutput(QgsProcessingOutputString(SERVABLE_ASSET_ID, tr("Display-ready copy (raster), when one was made")))

    # -- interface thread ---------------------------------------------------

    def prepareAlgorithm(self, parameters, context, feedback):  # noqa: N802 — name imposed by the QGIS API
        if not self._session.is_signed_in():
            raise QgsProcessingException(tr("Sign in to ALIGN first (Web menu › ALIGN › Sign in to ALIGN…)."))
        layer = self.parameterAsLayer(parameters, INPUT, context)
        if layer is None:
            raise QgsProcessingException(tr("No layer given."))

        verdict = support.verdict(layer)
        if verdict.reference is not None:
            raise QgsProcessingException(
                tr("“{}” is a service layer: register it as a reference from the ALIGN panel.").format(layer.name())
            )
        if not verdict.publishable:
            raise QgsProcessingException(verdict.reason)

        self._org = resolve_organization(self._session, self.parameterAsString(parameters, ORGANIZATION, context))

        selected_only = self.parameterAsBool(parameters, SELECTED_ONLY, context)
        brief = briefing.brief(layer, selected_only)
        title = (self.parameterAsString(parameters, TITLE, context) or "").strip() or brief.title or layer.name()
        description = (self.parameterAsString(parameters, DESCRIPTION, context) or "").strip() or None
        visibility = VISIBILITIES[self.parameterAsEnum(parameters, VISIBILITY, context)]
        feedback.pushInfo(brief.text)

        self._task = PublishLayerTask(
            self._session,
            layer,
            self._org["id"],
            self._org["name"],
            title,
            selected_only=selected_only,
            description=description,
            visibility=visibility,
            style=brief.style,
            iso=brief.iso,
        )
        return True

    # -- worker thread ------------------------------------------------------

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802 — name imposed by the QGIS API
        task = self._task
        task.progressChanged.connect(feedback.setProgress)
        feedback.canceled.connect(task.cancel)
        succeeded = task.run()
        if task.was_cancelled:
            raise QgsProcessingException(tr("Publishing cancelled. Nothing was catalogued."))
        if not succeeded:
            raise QgsProcessingException(task.error_message or tr("Publishing failed."))
        feedback.pushInfo(outcome_of(task, True).text)
        return {
            ASSET_ID: task.asset_id,
            ALIGN_URL: config.item_url(self._org["id"], task.asset_id),
            SERVABLE_ASSET_ID: task.servable_asset_id or "",
        }


def resolve_organization(session, wanted):
    """The organization an id or a name designates, among the user's.

    An id wins; a name must match exactly one organization, case-insensitively.
    Every other case is a refusal that lists what exists, so a model author
    can fix the parameter without opening ALIGN.
    """
    wanted = (wanted or "").strip()
    if not wanted:
        raise QgsProcessingException(tr("Give the organization to publish into, by name or by id."))
    orgs = api.list_organizations(session)
    by_id = [o for o in orgs if o["id"] == wanted]
    if by_id:
        return by_id[0]
    by_name = [o for o in orgs if o["name"].strip().lower() == wanted.lower()]
    if len(by_name) == 1:
        return by_name[0]
    names = ", ".join("{} ({})".format(o["name"], o["id"]) for o in orgs) or tr("none")
    if len(by_name) > 1:
        raise QgsProcessingException(tr("Several organizations are named “{}”; give the id. Yours: {}").format(wanted, names))
    raise QgsProcessingException(tr("No organization “{}” among yours: {}").format(wanted, names))
