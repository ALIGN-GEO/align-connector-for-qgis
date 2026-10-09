# -*- coding: utf-8 -*-
"""The Processing provider of the plugin (TCK-2181, ADR-0058 §7.1 level A).

What the panel does with clicks, Processing does with parameters: the
Modeler, the batch runner, ``processing.run()`` from the Python console, and
every MCP bridge that exposes ``execute_processing`` can publish a layer
without a line of code on our side. The algorithm reuses the publish task
of the panel; nothing is published twice in two ways.

Copyright (C) 2026 Geo Nova
SPDX-License-Identifier: GPL-2.0-or-later
"""
