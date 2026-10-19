#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove, inside QGIS, that a service layer takes its input rules (TCK-2343).

    python scripts/smoke_service_form_qgis.py

Needs the Python of a QGIS install. The rules are ALIGN's golden fixture
(``scripts/fixtures/feature-layer-form.golden.json``), set on a layer with the
service's fields. What is under test:

* each rule becomes the QGIS widget, alias, "not null" and constraint
  expression the translation says (CA-1, CA-2, CA-3);
* "visible if" puts the field in a container of its own with the condition,
  in a drag-and-drop form; no condition, the form stays generated (CA-4);
* set again with other rules, nothing of the old ones is left: the service
  is the source (CA-5);
* a refused save is explained from the edits waiting: feature, field and
  only the rules its value breaks (CA-6);
* a real save refused by ALIGN for its rules (a 422, against a local server
  that stands for ALIGN): QGIS's bare "Could not commit changes" for that
  layer is taken back and the plugin explains it, in ALIGN's words when
  QGIS passes them on (3.44 does, 3.34 does not); the edits stay (CA-6);
* the plugin's service actions: a layer opens with its rules, what ALIGN
  alone checks said once; "Edit in QGIS" and a project read set them again
  as ALIGN has them now, one read per service, without the per-layer counts
  (CA-3, CA-5). Unsigned here, so it runs below QGIS 3.36 too; the signed
  path is ``smoke_service_layers_qgis.py``.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import QgsApplication, QgsFeature, QgsFieldConstraints, QgsGeometry, QgsPointXY, QgsVectorLayer  # noqa: E402
from qgis.PyQt.QtCore import QObject, pyqtSignal  # noqa: E402

from ogc_stub import LAYER, SERVICE, Recorder, serve  # noqa: E402

GOLDEN = json.loads((REPO / "scripts" / "fixtures" / "feature-layer-form.golden.json").read_text(encoding="utf-8"))
QGIS_TYPES = {"string": "string", "number": "double", "integer": "integer", "boolean": "boolean", "date": "date"}


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def service_layer():
    fields = "&".join("field={}:{}".format(f["name"], QGIS_TYPES[f["type"]]) for f in GOLDEN["fields"])
    layer = QgsVectorLayer("Point?crs=EPSG:4326&" + fields, "Jardins (ALIGN)", "memory")
    check(layer.isValid(), "the layer opens")
    return layer


def test_the_rules_are_set(service_form):
    layer = service_layer()
    left = service_form.apply_form(layer, {"fields": GOLDEN["fields"], "form": GOLDEN["stored"]})
    check(left == [], "every rule of the fixture is translated: {}".format(left))
    fields = layer.fields()

    def setup(name):
        return layer.editorWidgetSetup(fields.indexOf(name))

    check(setup("etat").type() == "ValueMap" and setup("etat").config()["map"] == [{"Open": "ouvert"}, {"Closed": "ferme"}], "a list, with its labels")
    check(setup("surface").type() == "Range" and setup("surface").config()["Max"] == 5000, "a range, with its bounds")
    check(setup("bio").type() == "CheckBox", "a check box")
    check(setup("ouverture").type() == "DateTime" and setup("ouverture").config()["calendar_popup"], "a calendar")
    check(layer.attributeDisplayName(fields.indexOf("nom")) == "Garden name", "the alias of the rule")
    nom = fields.field("nom").constraints()
    check(nom.constraints() & QgsFieldConstraints.ConstraintNotNull, "required is not null")
    check(nom.constraintExpression() == "(\"nom\" IS NOT NULL AND \"nom\" <> '')", nom.constraintExpression())
    surface = fields.field("surface").constraints()
    check(surface.constraintDescription() == "Must be between 0 and 5000; A surface above zero", surface.constraintDescription())

    form = layer.editFormConfig()
    tabs = form.tabs()
    names = [tab.name() for tab in tabs]
    check(names == [f["name"] for f in GOLDEN["fields"]], "the drag-and-drop form keeps the fields in order: {}".format(names))
    container = tabs[names.index("ouverture")]
    check(container.visibilityExpression().enabled() and container.visibilityExpression().data().expression() == '("bio" = TRUE)', "visible if bio")
    return layer


def test_the_service_is_the_source(service_form, layer):
    rules = {"fields": [{"field": "essence", "widget": "valueMap", "valueMap": [{"value": "chene"}]}]}
    service_form.apply_form(layer, {"fields": GOLDEN["fields"], "form": rules})
    fields = layer.fields()
    check(layer.editorWidgetSetup(fields.indexOf("etat")).type() == "", "a rule ALIGN dropped leaves QGIS")
    check(not fields.field("nom").constraints().constraints() & QgsFieldConstraints.ConstraintNotNull, "required left with it")
    check(layer.attributeDisplayName(fields.indexOf("nom")) == "Nom du jardin", "back to the field's own alias")
    check(layer.editorWidgetSetup(fields.indexOf("essence")).type() == "ValueMap", "the new rule is set")
    check(layer.editFormConfig().layout() == service_form._layout("AutoGenerated"), "no 'visible if' left: the form is generated again")
    check(set(json.loads(layer.customProperty(service_form._CHECKS))) == {"essence"}, "the rules kept one by one are the new ones")


def test_a_refusal_is_explained(service_form):
    layer = service_layer()
    service_form.apply_form(layer, {"fields": GOLDEN["fields"], "form": GOLDEN["stored"]})
    layer.startEditing()
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(4.35, 50.8)))
    feature.setAttribute("surface", 9000)
    feature.setAttribute("etat", "inconnu")
    layer.addFeature(feature)
    lines = service_form.explain_refusal(layer)
    check(lines == [
        "New feature, Garden name: A value is required",
        "New feature, surface: Must be between 0 and 5000",
        "New feature, etat: Must be one of the list",
    ], "only the rules each value breaks: {}".format(lines))
    layer.rollBack()


class _Item(object):
    def __init__(self, title, text):
        self._title, self._text = title, text

    def title(self):
        return self._title

    def text(self):
        return self._text


class _Bar(QObject):
    widgetAdded = pyqtSignal(object)  # noqa: N815 — QGIS's name

    def __init__(self):
        super().__init__()
        self.popped = []

    def popWidget(self, item):  # noqa: N802 — QGIS's name
        self.popped.append(item)


class _Iface(object):
    def __init__(self):
        self.bar = _Bar()

    def messageBar(self):  # noqa: N802 — QGIS's name
        return self.bar


class _Session(object):
    def is_signed_in(self):
        return True


def test_a_refused_save_is_taken_back_and_explained(app, service_form):
    from align_connector.publish import service_memory
    from align_connector.session_watch import SessionWatch

    server = serve()
    Recorder.seen, Recorder.require_token = [], False
    Recorder.items_answer = (422, {"code": "InvalidFeature", "description": "nom: Needs at least 3 letters"})
    url = "http://127.0.0.1:{}{}".format(server.server_address[1], SERVICE)
    layer = QgsVectorLayer("url='{}' typename='{}'".format(url, LAYER), "Jardins (ALIGN)", "OAPIF")
    check(layer.isValid() and layer.fields().indexOf("nom") >= 0, "the service layer opens")
    rule = {"field": "nom", "widget": "text", "required": True, "constraintExpression": '[">=", ["length", ["get", "nom"]], 3]', "constraintDescription": "Needs at least 3 letters"}
    service_form.apply_form(layer, {"fields": [{"name": "nom", "type": "string"}], "form": {"fields": [rule]}})
    ref = service_memory.ServiceRef("org-1", "svc-jardins", LAYER, "Jardins", None, service_memory.SERVICE)

    iface, refused = _Iface(), []
    watch = SessionWatch(iface, _Session(), lambda: [(layer, ref)], on_rules_refusal=refused.append)
    watch.attach()
    check(layer.startEditing(), "in editing mode")
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(2.31, 48.86)))
    feature.setAttribute("nom", "ab")
    check(layer.addFeature(feature), "a feature in the buffer: QGIS's own form is not the only way in")
    check(not layer.commitChanges(False), "ALIGN refuses the save")
    passed_on = any("Server response:" in line for line in layer.commitErrors())
    check(service_form.align_answer(layer) == ("nom: Needs at least 3 letters" if passed_on else None), "ALIGN's own answer, when QGIS passes it on: {}".format(layer.commitErrors()))

    bare = _Item("", "Could not commit changes to layer Jardins (ALIGN)")
    other = _Item("", "Could not commit changes to layer roads")
    iface.bar.widgetAdded.emit(bare)
    iface.bar.widgetAdded.emit(other)
    app.processEvents()
    check(iface.bar.popped == [bare] and refused == [layer], "QGIS's bare message for the ALIGN layer goes, the other stays: {}".format(iface.bar.popped))
    check(service_form.explain_refusal(layer) == ["New feature, nom: Needs at least 3 letters"], service_form.explain_refusal(layer))
    from unittest.mock import MagicMock

    from align_connector.service_actions import ServiceLayerActions

    said = []
    ServiceLayerActions(MagicMock(), MagicMock(), lambda text, level: said.append(text))._explain_refusal(layer)
    reason = "nom: Needs at least 3 letters" if passed_on else "New feature, nom: Needs at least 3 letters"
    check(said == ["ALIGN refused to save “Jardins (ALIGN)”: {}. The edits stay in the layer: correct them and save again.".format(reason)], said)
    check(layer.isEditable() and len(layer.editBuffer().addedFeatures()) == 1, "the edit stays, to be corrected")

    layer.rollBack()
    watch.detach()
    Recorder.items_answer = None
    server.shutdown()


def test_the_actions_set_the_rules_and_follow_align(service_form):
    from unittest.mock import MagicMock

    from align_connector import config, service_actions
    from align_connector.publish import service_layer, service_memory
    from qgis.core import QgsProject

    server = serve()
    Recorder.seen, Recorder.require_token, Recorder.items_answer = [], False, None
    previous = (config.app_url(), config.api_url())
    config.set_endpoints(previous[0], "http://127.0.0.1:{}".format(server.server_address[1]))
    inside = {"field": "nom", "widget": "text", "required": True, "alias": "Garden name", "constraintExpression": '["within", {"type": "Polygon"}]', "constraintDescription": "Inside the park"}
    current = {"form": {"fields": [inside]}}
    reads = []

    def describe(_session, _org, service, counts=True):
        reads.append((service, counts))
        return {"callerMay": ["add", "update", "delete"], "layers": [{"layerId": LAYER, "fields": [{"name": "nom", "type": "string"}], "form": current["form"]}]}

    real_describe, service_actions.describe_service = service_actions.describe_service, describe
    session = MagicMock()
    session.keep_signer_fresh.return_value = True
    messages = []
    actions = service_actions.ServiceLayerActions(MagicMock(), session, lambda text, level: messages.append(text))
    try:
        ref = service_memory.ServiceRef("org-1", "svc-jardins", LAYER, "jardins", None, service_memory.SERVICE)
        layer = actions.open(ref, read_only=True)
        nom = layer.fields().indexOf("nom")
        check(layer.attributeDisplayName(nom) == "Garden name", "opened with its rules: {}".format(messages))
        check(layer.fields().field("nom").constraints().constraints() & QgsFieldConstraints.ConstraintNotNull, "required")
        check(messages == ["ALIGN checks these rules of “jardins (ALIGN, read-only)” only when you save: Garden name: Inside the park."], messages)
        check(reads == [("svc-jardins", False)], "one read, without the per-layer counts: {}".format(reads))

        current["form"] = {"fields": [{"field": "nom", "widget": "valueMap", "valueMap": [{"value": "a"}]}]}
        second = actions.open(ref)
        messages.clear()
        reads.clear()
        actions._on_project_read()
        for opened in (layer, second):
            check(opened.editorWidgetSetup(nom).type() == "ValueMap" and opened.attributeDisplayName(nom) == "nom", "the project read sets the rules ALIGN has now (CA-5)")
        check(reads == [("svc-jardins", False)], "one read per service: {}".format(reads))
        check(messages == [], "said once, at the opening: {}".format(messages))

        current["form"] = {"fields": [{"field": "nom", "widget": "text", "required": True}]}
        actions.edit_layer(second)
        check(second.isEditable() and second.fields().field("nom").constraints().constraints() & QgsFieldConstraints.ConstraintNotNull, "“Edit in QGIS” sets them again")
        check(second.editorWidgetSetup(nom).type() == "TextEdit", "the list ALIGN dropped is gone")
        second.rollBack()
        check(len(service_layer.service_layers()) == 2, "two service layers in the project")
    finally:
        service_actions.describe_service = real_describe
        QgsProject.instance().clear()
        config.set_endpoints(*previous)
        server.shutdown()


def main():
    app = QgsApplication([], False)
    app.initQgis()
    from align_connector.publish import service_form

    layer = test_the_rules_are_set(service_form)
    print("ok  test_the_rules_are_set")
    test_the_service_is_the_source(service_form, layer)
    print("ok  test_the_service_is_the_source")
    test_a_refusal_is_explained(service_form)
    print("ok  test_a_refusal_is_explained")
    test_a_refused_save_is_taken_back_and_explained(app, service_form)
    print("ok  test_a_refused_save_is_taken_back_and_explained")
    test_the_actions_set_the_rules_and_follow_align(service_form)
    print("ok  test_the_actions_set_the_rules_and_follow_align")
    print("5 checks passed")
    app.exitQgis()


if __name__ == "__main__":
    main()
