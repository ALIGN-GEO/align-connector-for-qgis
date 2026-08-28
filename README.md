# ALIGN Connector for QGIS

Publish QGIS layers to your [ALIGN](https://aligngeo.com) organization's Datastore — vector and raster — without leaving QGIS.

> **Status: early development, not yet distributed.** Signing in and publishing a vector layer are implemented and exercised in QGIS 3.44 LTR; they have not yet been run end to end against a live ALIGN account. Raster publishing and browsing your Datastore from QGIS come next.

## What it does

You work in QGIS. Your data belongs in ALIGN, where your team consumes it and your web maps are built from it. Today that means exporting a file and uploading it through a browser. This plugin removes that round trip:

1. **Sign in once** — the plugin opens your browser, you approve, and it comes back with a token. No password, no API key to copy, nothing to paste.
2. **Pick a layer and an organization** — publish.

Large layers keep uploading in the background: you carry on working, the progress is visible, and an interrupted transfer resumes where it stopped rather than starting over.

Your ALIGN role decides what you may do. The plugin never grants access: it carries your identity, and shows you what the server answers. An organization viewer is refused, exactly as in the web app.

## Requirements

- QGIS **3.22 LTR or newer** (developed against 3.44 LTR, Qt5, Python 3.12)
- An [ALIGN](https://aligngeo.com) account, and membership of at least one organization with a role that can publish

No third-party Python packages. Nothing to `pip install`.

## Installation

Not yet published to the QGIS plugin repository. Until then, clone this repository and copy (or symlink) the `align_connector/` folder into your QGIS profile:

| Platform | Path |
|---|---|
| Windows | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\` |
| macOS | `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/` |
| Linux | `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/` |

Then enable **ALIGN Connector** in *Plugins → Manage and Install Plugins → Installed*.

## Security

- Tokens are stored in QGIS' own encrypted authentication database (`QgsAuthManager`). Only an authentication configuration id is kept in the plugin's settings — never a token, and never in your `.qgs` project file.
- The plugin holds a token scoped to publishing only. It cannot read or download your organization's data.
- Every session is revocable from ALIGN (*Settings → Connected devices*), and signing out revokes it server-side too.

Reading ALIGN layers from QGIS does not need this plugin: ALIGN serves **OGC API - Features**, which QGIS opens natively.

## Contributing

Issues and pull requests are welcome. The plugin is free software; the ALIGN service it talks to is a commercial product — the same arrangement as most QGIS connectors to hosted platforms.

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).

Copyright (C) 2026 Geo Nova.
