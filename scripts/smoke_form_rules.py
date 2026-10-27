#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove the translation of a service layer's input rules into QGIS settings
(TCK-2343).

    python scripts/smoke_form_rules.py

Runs on a plain interpreter — no QGIS. The rules are ALIGN's own: the golden
fixture of the form (TCK-2330, ``feature-layer-form.golden.json`` in ALIGN's
orchestrator, copied verbatim under ``scripts/fixtures/``; refresh the copy
when ALIGN changes it). What is under test:

* each input type becomes its QGIS widget (list with its labels, slider,
  check box, calendar), with the alias;
* "required" is not null and not empty text, as ALIGN reads it;
* bounds, list membership and a condition become one constraint expression
  with its message; a condition the plugin cannot translate is left to ALIGN,
  and named;
* "visible if" becomes a container's visibility, and the field's rules hold
  only while it is shown, as ALIGN skips a hidden field; an untranslatable
  "visible if" leaves every rule of the field to ALIGN.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from align_connector.form_rules import VALUE_MAP_NULL, field_settings, form_settings, qgis_expression, rules_refusal  # noqa: E402

GOLDEN = json.loads((REPO / "scripts" / "fixtures" / "feature-layer-form.golden.json").read_text(encoding="utf-8"))


def by_field(settings):
    return {entry["field"]: entry for entry in settings}


def test_the_golden_form_becomes_qgis_settings():
    settings = by_field(form_settings(GOLDEN["stored"], GOLDEN["fields"]))
    assert set(settings) == {"nom", "surface", "etat", "bio", "ouverture"}, settings.keys()

    nom = settings["nom"]
    assert (nom["widget"], nom["alias"], nom["not_null"]) == ("TextEdit", "Garden name", True), nom
    assert nom["expression"] == "(\"nom\" IS NOT NULL AND \"nom\" <> '')", nom["expression"]
    assert nom["description"] == "A value is required"

    surface = settings["surface"]
    assert surface["widget"] == "Range" and surface["config"] == {"Style": "Slider", "AllowNull": True, "Min": 0, "Max": 5000, "Step": 10}, surface
    assert surface["expression"] == '("surface" IS NULL OR ("surface" >= 0 AND "surface" <= 5000)) AND ("surface" > 0)', surface["expression"]
    assert surface["description"] == "Must be between 0 and 5000; A surface above zero"
    assert surface["left_to_server"] == []
    assert surface["checks"] == [
        {"expression": '("surface" IS NULL OR ("surface" >= 0 AND "surface" <= 5000))', "message": "Must be between 0 and 5000"},
        {"expression": '("surface" > 0)', "message": "A surface above zero"},
    ], "each rule on its own, to name the one a value breaks: {}".format(surface["checks"])

    etat = settings["etat"]
    assert etat["widget"] == "ValueMap" and etat["config"] == {"map": [{"Open": "ouvert"}, {"Closed": "ferme"}]}, etat
    assert etat["not_null"] is True
    assert etat["expression"] == "(\"etat\" IS NOT NULL AND \"etat\" <> '') AND (\"etat\" IS NULL OR to_string(\"etat\") IN ('ouvert', 'ferme'))", etat["expression"]

    assert settings["bio"]["widget"] == "CheckBox" and settings["bio"]["expression"] is None

    ouverture = settings["ouverture"]
    assert ouverture["widget"] == "DateTime" and ouverture["config"]["field_format"] == "yyyy-MM-dd", ouverture
    assert ouverture["visibility"] == '("bio" = TRUE)', ouverture["visibility"]


def test_a_field_the_layer_does_not_have_is_skipped_and_an_optional_list_can_stay_empty():
    form = {"fields": [{"field": "ghost", "widget": "text", "required": True}, {"field": "etat", "widget": "valueMap", "valueMap": [{"value": "a"}]}]}
    settings = form_settings(form, [{"name": "etat", "type": "string"}])
    assert [entry["field"] for entry in settings] == ["etat"]
    assert settings[0]["config"]["map"] == [{"<NULL>": VALUE_MAP_NULL}, {"a": "a"}], settings[0]["config"]


def test_a_required_hidden_field_holds_only_while_shown():
    rule = {"field": "motif", "widget": "text", "required": True, "visibilityExpression": '["==", ["get", "etat"], "ferme"]'}
    settings = field_settings(rule, "string")
    assert settings["not_null"] is False, "a bare not-null would refuse what ALIGN accepts"
    assert settings["visibility"] == "(\"etat\" = 'ferme')"
    assert settings["expression"] == "(NOT (\"etat\" = 'ferme') OR (\"motif\" IS NOT NULL AND \"motif\" <> ''))", settings["expression"]
    assert [check["expression"] for check in settings["checks"]] == [settings["expression"]], settings["checks"]


def test_what_cannot_be_translated_is_left_to_align_and_named():
    condition = {"field": "h", "widget": "number", "constraintExpression": '["within", {"type": "Polygon"}]', "constraintDescription": "Inside the park"}
    settings = field_settings(condition, "number")
    assert settings["expression"] is None and settings["left_to_server"] == ["Inside the park"], settings

    hidden = {"field": "h", "widget": "number", "min": 0, "visibilityExpression": '["coalesce", ["get", "x"], 1]'}
    settings = field_settings(hidden, "number")
    assert settings["visibility"] is None and settings["expression"] is None and settings["left_to_server"] == ["Its rules, while it is shown"], settings
    assert settings["checks"] == [], "nothing to check in QGIS"


def test_the_expressions_the_plugin_knows():
    cases = {
        '[">", ["get", "surface"], 0]': '("surface" > 0)',
        '["all", ["!=", ["get", "a"], null], ["<=", ["*", ["get", "b"], 2], 10]]': '(("a" IS NOT NULL) AND (("b" * 2) <= 10))',
        '["any", ["in", ["get", "etat"], ["literal", ["a", "b"]]], ["!", ["get", "bio"]]]': "((\"etat\" IN ('a', 'b')) OR (NOT \"bio\"))",
        '[">=", ["length", ["to-string", ["get", "nom"]]], 3]': '(length(to_string("nom")) >= 3)',
        '["==", ["get", "it\'s"], "l\'eau"]': "(\"it's\" = 'l''eau')",
        '["==", ["get", "a\\"b"], 1]': '("a""b" = 1)',
    }
    for source, expected in cases.items():
        assert qgis_expression(source) == expected, (source, qgis_expression(source))
    for unknown in ('["coalesce", ["get", "a"], 1]', '["in", "a", "abc"]', '["get", "a", {"x": 1}]', "not json", "", '"text"', '["case", true, 1, 2]'):
        assert qgis_expression(unknown) is None, unknown


def test_the_separators_follow_the_language():
    french = {"; ": "\u00a0; ", "Must be between {} and {}": "Doit être entre {} et {}"}
    settings = by_field(form_settings(GOLDEN["stored"], GOLDEN["fields"], lambda text: french.get(text, text)))
    assert settings["surface"]["description"] == "Doit être entre 0 et 5000\u00a0; A surface above zero", settings["surface"]["description"]


#: What QGIS's OAPIF provider leaves in ``commitErrors()`` when ALIGN refuses a
#: new feature for its rules, as measured: QGIS 3.44 passes ALIGN's answer on
#: (here as Fastify sends it, compact), QGIS 3.34 only the reason phrase.
QGIS_344 = [
    "ERROR: 1 feature(s) not added.",
    "\n  Provider errors:",
    "    Feature creation failed: Create Feature request failed: Error transferring https://api/x/items - server replied: Unprocessable Entity\n"
    '    Server response: {"code":"InvalidFeature","description":"nom: Needs at least 3 letters; etat: Must be one of the list"}',
]
QGIS_334 = ["ERROR: 1 feature(s) not added.", "    Feature creation failed: Create Feature request failed: Error transferring https://api/x/items - server replied: Unprocessable Entity"]


def test_a_refusal_for_the_rules_is_recognised_and_alignS_answer_kept():
    assert rules_refusal(QGIS_344) == (True, "nom: Needs at least 3 letters; etat: Must be one of the list")
    assert rules_refusal(QGIS_334) == (True, None), "without ALIGN's answer, the reason phrase says it"
    http2 = [QGIS_344[0], QGIS_344[2].replace("Unprocessable Entity", "")]
    assert rules_refusal(http2)[0], "no reason phrase over HTTP/2: ALIGN's code says it"
    renamed = [QGIS_334[1].replace("Entity", "Content")]
    assert rules_refusal(renamed) == (True, None), "RFC 9110 calls it Unprocessable Content"
    other = ['    Server response: {"code":"PreconditionFailed","description":"The feature changed since it was read"}', "server replied: Precondition Failed"]
    assert rules_refusal(other) == (False, None), "a conflict is not a refusal for the rules"
    assert rules_refusal(["Server response: {not json"]) == (False, None)
    assert rules_refusal([]) == (False, None)


def main():
    tests = [
        test_the_golden_form_becomes_qgis_settings,
        test_a_field_the_layer_does_not_have_is_skipped_and_an_optional_list_can_stay_empty,
        test_a_required_hidden_field_holds_only_while_shown,
        test_what_cannot_be_translated_is_left_to_align_and_named,
        test_the_expressions_the_plugin_knows,
        test_the_separators_follow_the_language,
        test_a_refusal_for_the_rules_is_recognised_and_alignS_answer_kept,
    ]
    for test in tests:
        test()
        print("ok  {}".format(test.__name__))
    print("{} checks passed".format(len(tests)))


if __name__ == "__main__":
    main()
