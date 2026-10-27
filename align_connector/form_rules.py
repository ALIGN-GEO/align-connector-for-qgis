# -*- coding: utf-8 -*-
"""The input rules of an ALIGN feature service, as QGIS settings (TCK-2343).

A service layer's form (TCK-2330) is enforced by ALIGN at every write, QGIS
included. Read here, it becomes what QGIS shows while one types: the input
widget, "not null", a constraint expression and its message, the alias, and
"visible if" (by a container, the only place QGIS takes a visibility
condition). ALIGN stays the judge: what this module cannot translate is left
to the server, and said once.

Pure Python, no QGIS import: the translation is tested on a plain
interpreter against a copy of ALIGN's golden fixture of the form
(``scripts/fixtures/feature-layer-form.golden.json``).

Two rules of ALIGN shape the translation:

* **A hidden field is not checked.** ALIGN skips the rules of a field whose
  "visible if" is false. Its QGIS constraint is therefore
  ``NOT (visible) OR (rules)``, never a bare "not null" that would refuse a
  record ALIGN accepts. A field whose "visible if" cannot be translated gets
  no constraint at all in QGIS: ALIGN alone judges it.
* **Empty means missing.** A required value is neither NULL nor an empty
  text, as ALIGN reads it.

Expressions are MapLibre expressions (JSON). The translation names what it
knows (an allowlist): reading an attribute, literals, comparisons, ``all``,
``any``, ``!``, ``in`` a literal list, arithmetic, ``to-number``,
``to-string``, ``length``. Anything else is left to the server.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
import json

#: QGIS' value for "no value" in a value-map list (QgsValueMapFieldFormatter.NULL_VALUE).
VALUE_MAP_NULL = "{2839923C-8B7D-419E-B84B-CA2FE9B80EC7}"

_COMPARISONS = {"==": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
_ARITHMETIC = {"+": "+", "-": "-", "*": "*", "/": "/", "%": "%"}
_FUNCTIONS = {"to-number": "to_real", "to-string": "to_string", "length": "length"}
#: ALIGN's code for a feature its rules refuse (the OGC exception of its 422).
_RULES_CODE = "InvalidFeature"
#: Where QGIS's OAPIF provider puts the server's answer in a commit error, when it does (3.44 does, 3.34 does not).
_SERVER_RESPONSE = "Server response:"
#: The reason phrase of a 422: "Unprocessable Entity" (RFC 4918), "Unprocessable Content" (RFC 9110); none over HTTP/2.
_RULES_PHRASE = "Unprocessable"


def quote_field(name):
    """A field name as a QGIS expression reads it."""
    return '"{}"'.format(str(name).replace('"', '""'))


def _literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'{}'".format(value.replace("'", "''"))
    return None


def _translate(node):
    """A MapLibre expression node as a QGIS expression, or ``None``."""
    if not isinstance(node, list):
        return _literal(node)
    if not node or not isinstance(node[0], str):
        return None
    op, args = node[0], node[1:]
    if op == "get" and len(args) == 1 and isinstance(args[0], str):
        return quote_field(args[0])
    if op == "literal" and len(args) == 1 and not isinstance(args[0], (list, dict)):
        return _literal(args[0])
    if op in _COMPARISONS and len(args) == 2:
        if op in ("==", "!=") and (args[0] is None or args[1] is None):
            other = _translate(args[1] if args[0] is None else args[0])
            if other is None:
                return None
            return "({} {})".format(other, "IS NULL" if op == "==" else "IS NOT NULL")
        left, right = _translate(args[0]), _translate(args[1])
        if left is None or right is None:
            return None
        return "({} {} {})".format(left, _COMPARISONS[op], right)
    if op in ("all", "any") and args:
        parts = [_translate(arg) for arg in args]
        if any(part is None for part in parts):
            return None
        return "({})".format(" {} ".format("AND" if op == "all" else "OR").join(parts))
    if op == "!" and len(args) == 1:
        inner = _translate(args[0])
        return None if inner is None else "(NOT {})".format(inner)
    if op == "in" and len(args) == 2:
        haystack = args[1]
        if not (isinstance(haystack, list) and len(haystack) == 2 and haystack[0] == "literal" and isinstance(haystack[1], list)):
            return None
        needle = _translate(args[0])
        values = [_literal(value) for value in haystack[1]]
        if needle is None or not values or any(value is None for value in values):
            return None
        return "({} IN ({}))".format(needle, ", ".join(values))
    if op in _ARITHMETIC and len(args) >= 2:
        parts = [_translate(arg) for arg in args]
        if any(part is None for part in parts):
            return None
        return "({})".format(" {} ".format(_ARITHMETIC[op]).join(parts))
    if op in _FUNCTIONS and len(args) == 1:
        inner = _translate(args[0])
        return None if inner is None else "{}({})".format(_FUNCTIONS[op], inner)
    return None


def qgis_expression(source):
    """A MapLibre expression (as ALIGN stores it: a JSON text, or already
    parsed) as a QGIS expression, or ``None`` when it is not one this module
    knows."""
    if isinstance(source, str):
        text = source.strip()
        if not text:
            return None
        try:
            source = json.loads(text)
        except ValueError:
            return None
    if not isinstance(source, list):
        return None
    return _translate(source)


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def widget_of(rule, field_type):
    """The QGIS editor widget for a rule: ``(type, config)``."""
    widget = rule.get("widget")
    lo, hi, step = _number(rule.get("min")), _number(rule.get("max")), _number(rule.get("step"))
    if widget == "valueMap":
        entries = [entry for entry in rule.get("valueMap") or [] if isinstance(entry, dict) and isinstance(entry.get("value"), str)]
        mapping = [{(entry.get("label") or entry["value"]): entry["value"]} for entry in entries]
        if not rule.get("required"):
            mapping.insert(0, {"<NULL>": VALUE_MAP_NULL})
        return "ValueMap", {"map": mapping}
    if widget in ("number", "range"):
        config = {"Style": "Slider" if widget == "range" else "SpinBox", "AllowNull": True}
        if lo is not None:
            config["Min"] = lo
        if hi is not None:
            config["Max"] = hi
        if step is not None:
            config["Step"] = step
        elif field_type == "integer":
            config["Step"] = 1
        return "Range", config
    if widget == "checkbox":
        return "CheckBox", {"CheckedState": "", "UncheckedState": "", "TextDisplayMethod": 0}
    if widget == "date":
        fmt = "yyyy-MM-ddTHH:mm:ss" if field_type == "datetime" else "yyyy-MM-dd"
        shown = "yyyy-MM-dd HH:mm" if field_type == "datetime" else "yyyy-MM-dd"
        return "DateTime", {"field_format": fmt, "display_format": shown, "calendar_popup": True, "allow_null": True}
    return "TextEdit", {"IsMultiline": False, "UseHtml": False}


def _rule_checks(rule, name, field_type, tr):
    """The parts of a field's constraint, as ``(expression, message)``, and the
    condition left to the server (its message), if any."""
    field = quote_field(name)
    parts = []
    widget = rule.get("widget")
    if rule.get("required") and widget != "checkbox":
        empty_text = " AND {} <> ''".format(field) if field_type == "string" else ""
        parts.append(("({} IS NOT NULL{})".format(field, empty_text), tr("A value is required")))
    lo, hi = _number(rule.get("min")), _number(rule.get("max"))
    if widget in ("number", "range") and (lo is not None or hi is not None):
        bounds = [b for b in ("{} >= {}".format(field, repr(lo)) if lo is not None else None, "{} <= {}".format(field, repr(hi)) if hi is not None else None) if b]
        if lo is not None and hi is not None:
            message = tr("Must be between {} and {}").format(lo, hi)
        elif lo is not None:
            message = tr("Must be at least {}").format(lo)
        else:
            message = tr("Must be at most {}").format(hi)
        parts.append(("({} IS NULL OR ({}))".format(field, " AND ".join(bounds)), message))
    values = [entry.get("value") for entry in rule.get("valueMap") or [] if isinstance(entry, dict) and isinstance(entry.get("value"), str)]
    if widget == "valueMap" and values:
        listed = ", ".join(_literal(value) for value in values)
        parts.append(("({} IS NULL OR to_string({}) IN ({}))".format(field, field, listed), tr("Must be one of the list")))
    left_to_server = None
    condition = rule.get("constraintExpression")
    if isinstance(condition, str) and condition.strip():
        translated = qgis_expression(condition)
        message = rule.get("constraintDescription") or tr("Does not meet its condition")
        if translated is None:
            left_to_server = message
        else:
            parts.append((translated, message))
    return parts, left_to_server


def field_settings(rule, field_type, tr=lambda text: text):
    """What QGIS should show for one field of a service layer.

    :param rule: the field's rule, as ALIGN's form stores it
    :param field_type: the field's type in the service (``string``, ``integer``…)
    :param tr: the plugin's translation function (identity in tests)
    :returns: a dict: ``widget`` and ``config`` (the editor widget), ``alias``
        (or ``None``), ``not_null`` (bool), ``expression`` and ``description``
        (or ``None``), ``visibility`` (a QGIS expression, or ``None``),
        ``checks``: each rule on its own (``{"expression", "message"}``), to
        name the one a value breaks, and ``left_to_server``: what ALIGN alone
        checks, in words (a list)
    """
    name = rule.get("field")
    widget, config = widget_of(rule, field_type)
    parts, condition_left = _rule_checks(rule, name, field_type, tr)
    left = [condition_left] if condition_left else []
    visibility_source = rule.get("visibilityExpression")
    visibility = None
    not_null = bool(rule.get("required")) and rule.get("widget") != "checkbox"
    if isinstance(visibility_source, str) and visibility_source.strip():
        visibility = qgis_expression(visibility_source)
        not_null = False
        if visibility is None:
            # ALIGN skips a hidden field's rules; when QGIS cannot tell when
            # the field is hidden, any rule here could refuse what ALIGN accepts.
            left = [tr("Its rules, while it is shown")]
            parts = []
        else:
            parts = [("(NOT {} OR {})".format(visibility, expression), message) for expression, message in parts]
    expression = " AND ".join(expression for expression, _message in parts) or None
    description = tr("; ").join(message for _expression, message in parts) or None
    return {
        "field": name,
        "widget": widget,
        "config": config,
        "alias": rule.get("alias") or None,
        "not_null": not_null,
        "expression": expression,
        "description": description,
        "visibility": visibility,
        "checks": [{"expression": expression, "message": message} for expression, message in parts],
        "left_to_server": left,
    }


def _json_object(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def rules_refusal(errors):
    """Whether a layer's commit errors are ALIGN refusing a feature for its
    rules, and ALIGN's own answer when QGIS passed it on.

    ALIGN's answer decides when it is there (its code); without it, the
    reason phrase of the 422 does.

    :param errors: the layer's ``commitErrors()``
    :returns: ``(refused, answer)``; ``answer`` is ALIGN's field-by-field
        message, or ``None``
    """
    lines = [str(line) for line in errors or []]
    bodies = [_json_object(line[line.find(_SERVER_RESPONSE) + len(_SERVER_RESPONSE):]) for line in lines if _SERVER_RESPONSE in line]
    bodies = [body for body in bodies if body is not None and "code" in body]
    if bodies:
        for body in bodies:
            if body.get("code") == _RULES_CODE:
                description = body.get("description")
                return True, description.strip() if isinstance(description, str) and description.strip() else None
        return False, None
    return any(_RULES_PHRASE in line for line in lines), None


def form_settings(form, fields, tr=lambda text: text):
    """The settings of every ruled field of a layer that the layer has.

    :param form: the layer's form (``{"fields": [rule…]}``) or ``None``
    :param fields: the layer's fields, as the service describes them (``[{"name", "type"}]``)
    """
    types = {field.get("name"): field.get("type") for field in fields or [] if isinstance(field, dict)}
    rules = (form or {}).get("fields") or []
    return [field_settings(rule, types[rule["field"]], tr) for rule in rules if isinstance(rule, dict) and rule.get("field") in types]
