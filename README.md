# ALIGN Connector for QGIS

Publish QGIS layers to your [ALIGN](https://aligngeo.com) organization's Datastore — vector and raster — without leaving QGIS.

> **Status: early development, not yet distributed.** Signing in and publishing vector and raster layers are implemented and proven end to end against a live ALIGN account. Browsing your Datastore from QGIS comes next.

## What it does

You work in QGIS. Your data belongs in ALIGN, where your team consumes it and your web maps are built from it. Today that means exporting a file and uploading it through a browser. This plugin removes that round trip:

1. **Sign in once** — the plugin opens your browser, you approve, and it comes back with a token. No password, no API key to copy, nothing to paste.
2. **Open the ALIGN panel** — tick one layer, several, or the whole project, pick an organization, publish. Each layer becomes its own publication: the panel's Activity view shows every one of them progressing, then what ALIGN made of it, with a link to the item. A layer's context menu offers the same entry for just that layer. Tick “Also create a web map” and, once every layer has landed, ALIGN assembles a web map of them in the Layers panel's order, with their visibility, their style and the view your canvas showed, ready to open in the Map builder. A layer the Map builder cannot show (a mesh, a point cloud, a WMS or XYZ connection, a table without geometry, curved geometries) is listed greyed with the reason, never silently skipped.

3. **Publish again, in place** — a layer you published remembers the item it became (in the project file, so it survives closing QGIS). Correct the layer, publish again: the panel offers to **replace** the item, same identifier, so every web map that shows it shows the new version; the previous version is kept on ALIGN, and the fields that were added, removed or retyped are named. Untick to publish a new item instead.

4. **Publish as an editable feature service** — where your organization's plan includes feature services, tick “Publish vector layers as editable feature services”. ALIGN moves the features into your organization's database (in their own coordinate system when it has an EPSG code) and serves them through OGC API Features; each layer becomes a new service, shown in the Datastore. Once it is ready, **Edit it in QGIS** puts the service's layer where the local one was, with its style, and hides the local layer without removing it: toggle editing, and your changes are saved on ALIGN, with who changed what and when. The Map builder and every other QGIS opening the service see them. QGIS sends no version with its edits, so the last save of a feature wins. Editing from QGIS needs QGIS 3.34 or later; before that, the plugin gives the service's address.

Large layers keep uploading in the background: you carry on working, the progress is visible, and an interrupted transfer resumes where it stopped rather than starting over.

Your ALIGN role decides what you may do. The plugin never grants access: it carries your identity, and shows you what the server answers. An organization viewer is refused, exactly as in the web app.

## What you can publish

| Your layer | What is sent | What ALIGN stores |
|---|---|---|
| Any vector layer QGIS can read | FlatGeobuf (GeoJSON where the driver is missing), in WGS 84 | one layer, served as it is |
| A vector layer read from a FlatGeobuf or GeoParquet file already in WGS 84 | that file, byte for byte | the same file, served as it is |
| A vector tile layer read from a PMTiles or MBTiles archive on disk | that archive, byte for byte | the PMTiles (an MBTiles is converted on arrival) |
| A raster already stored as a GeoTIFF | that file, byte for byte | a Cloud-Optimized GeoTIFF, prepared on arrival |
| A raster read from an MBTiles archive | that archive, byte for byte | the PMTiles; the Map builder shows vector tile archives only, so far |
| Any other raster GDAL can read | a compressed GeoTIFF written for the occasion | a Cloud-Optimized GeoTIFF |
| A layer that is a service: XYZ, WMS, WMTS, WFS, OGC API Features, ArcGIS Feature or Map Service, vector tiles, a remote COG or PMTiles, a point cloud or 3D Tiles by URL | its address and what names the layer, no data | a live reference, read when the map opens, one per service |

### By Map builder format

What the Map builder's Add Data offers, and what becomes of it from QGIS:

| Map builder format | From QGIS |
|---|---|
| Vector, Delimited text, CAD (DXF), GPX, OSM PBF | a QGIS vector layer: FlatGeobuf in WGS 84 (DWG is not read by QGIS; an OSM PBF is one layer per geometry family) |
| Raster | a GeoTIFF as it is, or one written from the layer; Cloud-Optimized on arrival |
| FlatGeobuf, GeoParquet | the file as it is when already in WGS 84, else FlatGeobuf in WGS 84 |
| MBTiles | the archive as it is; converted to PMTiles on arrival (raster tiles are stored but not shown yet) |
| PMTiles | a local archive as it is; a remote one as a live reference |
| XYZ, WMS, WMTS, WFS, OGC Vector Tiles, ArcGIS | a live reference (WMTS: Web Mercator tile matrix sets; vector tiles: the layers QGIS styles) |
| GeoRSS | a QGIS vector layer: a FlatGeobuf snapshot of the feed |
| NetCDF / HDF, Zarr | one variable written as a GeoTIFF (every time step a band), Cloud-Optimized on arrival |
| LiDAR, 3D Tiles | a live reference when the layer reads a URL; a local file cannot be published |
| Video, Deck.gl, Splatting, 3D Model (glTF), DuckDB | no QGIS counterpart: added from the ALIGN web application |

The list grows as each format is proven end to end rather than merely wired up.

Two things are worth knowing before you send a raster. ALIGN prepares it for
display when it arrives; **if that preparation has to reproject your data, your
original is kept alongside the display-ready copy** — nothing is silently
replaced, and both count against your organization's storage. And a service
layer is kept as a reference, the way ArcGIS Online keeps it: ALIGN stores the
address, reads the service when the map opens, and reuses one item for the
same service however many times it is published. A service whose address
carries a key or a signature is refused: a reference never stores a credential.

There is no size limit in the plugin. Your organization's remaining storage is
the limit, and it is checked **before the transfer starts** — a publish that
would not fit is refused in seconds, not after an hour of uploading.

## Automate

The plugin registers an **ALIGN** Processing provider with one algorithm, `align:publishlayer` (*Publish layer to ALIGN*): the same publication as the panel, with the layer's style and metadata, driven by parameters. Use it from the Modeler, the batch runner, the Python console, or any MCP bridge that lets an agent run Processing algorithms (QGIS MCP's `execute_processing`, for instance). Sign in from the panel first: the algorithm never opens a browser.

```python
import processing
result = processing.run("align:publishlayer", {
    "INPUT": "Countries", "ORGANIZATION": "Town GIS", "VISIBILITY": 1, "SELECTED_ONLY": False, "REPLACE": True,
})
print(result["ASSET_ID"], result["ALIGN_URL"])
```

The organization is given by name or id; a wrong one is refused with the list of yours. A service layer is refused with a pointer to the panel, which registers it as a reference. `REPLACE` (on by default) replaces the item the layer was published as, when it lives in that organization; off, every run makes a new item.

## Requirements

- QGIS **3.22 LTR or newer**, QGIS 4.x included: one source for Qt5 and Qt6 through the `qgis.PyQt` shim and `qtcompat.qt_enum`. Developed against 3.44 LTR (Qt5, Python 3.12); CI loads the plugin on the `qgis/qgis:ltr` and `qgis/qgis:4.2` images.
- An [ALIGN](https://aligngeo.com) account, and membership of at least one organization with a role that can publish
- To edit a feature service from QGIS: QGIS **3.34** or newer, the first version whose OAuth2 method signs with PKCE

No third-party Python packages. Nothing to `pip install`.

## Languages

The plugin speaks the language QGIS runs in (*Settings → Options → General*): **English** (the source) and **French**. A language without a translation reads as English. The refusals ALIGN sends back are composed by the plugin from the server's structured answer, so a « budget insuffisant » reads in your language with its numbers.

Adding a language is a translation file, no code: `python scripts/translations.py extract` refreshes `align_connector/i18n/align_connector_<lang>.ts` (open it in Qt Linguist or any editor), `python scripts/translations.py compile` writes the `.qm` the plugin loads, and `python scripts/translations.py check` is what CI runs: every sentence present and translated, the `.qm` up to date. Dutch and German come next, by the same files.

## Installation

The plugin is in **beta**, distributed from ALIGN's own plugin repository until it reaches the official QGIS one.

**From the ALIGN repository** (updates arrive in QGIS like any other plugin):

1. In QGIS, *Plugins → Manage and Install Plugins → Settings*, tick **Show also experimental plugins**, then under *Plugin repositories* click **Add**: name `ALIGN`, URL `https://aligngeo.com/qgis/plugins.xml`.
2. Back in *All*, search **ALIGN Connector** and click **Install Plugin**.

**From a ZIP**: download `align_connector.<version>.zip` from the same address (or from this repository's Releases), then *Plugins → Manage and Install Plugins → Install from ZIP*.

Either way, nothing else to install: no Python package, no key to paste. The [documentation](https://aligngeo.com/docs/integrations/qgis/) walks through signing in and publishing.

### Working on the plugin

On Windows, `scripts/dev_install.ps1` links this repository into your QGIS profile, so an edit here is picked up by reloading the plugin — no copying between iterations.

> **Do not use QGIS' "Uninstall" button on a linked install.** It deletes the plugin folder and follows the link, erasing the source files in this repository. To turn the plugin off, *uncheck* it in the plugin manager; to remove it for real, run `scripts/dev_install.ps1 -Uninstall`, which removes the link and never touches what it points at.
>
> If it already happened: `git restore align_connector/` brings everything back, as long as the work was committed.

### Releasing

1. In `align_connector/metadata.txt`, bump `version` and write the `changelog` entry. `python scripts/check_metadata.py` says whether the plugin repository would accept it.
2. `python scripts/package.py` builds `dist/align_connector.<version>.zip` (plugin folder with `LICENSE` inside, no compiled files) and its SHA-256, and verifies the zip the way QGIS will.
3. Tag `v<version>` and push the tag: the Release workflow rebuilds the zip with the same script and attaches it to a GitHub Release.
4. `python scripts/package.py --repository-xml https://aligngeo.com/qgis` prints the `plugins.xml` served by ALIGN's beta repository; it goes to the site with the zip and the icon.

## Security

- Tokens are stored in QGIS' own encrypted authentication database (`QgsAuthManager`). Only an authentication configuration id is kept in the plugin's settings — never a token, and never in your `.qgs` project file.
- The plugin holds a token scoped to publishing and to the feature services (`datastore:publish`, `features:read`, `features:write`). It cannot read or download the rest of your organization's Datastore, and a feature service only answers it within your role.
- A feature-service layer opened in QGIS signs its requests with QGIS' own OAuth2 method (authorization code with PKCE, a grant of its own limited to the features scopes). QGIS keeps and refreshes its tokens in the same encrypted database; the project file only holds the configuration's id. The first time, your browser asks you to approve it.
- Every session is revocable from ALIGN (*Settings → Connected devices*), and signing out revokes it server-side too.

Reading ALIGN layers from QGIS does not need this plugin: ALIGN serves **OGC API - Features**, which QGIS opens natively.

## Contributing

Issues and pull requests are welcome. The plugin is free software; the ALIGN service it talks to is a commercial product — the same arrangement as most QGIS connectors to hosted platforms.

Reporting a bug: in the ALIGN panel, open **Messages** and click **Copy diagnostic** (also in the toolbar button's menu), then paste it in the issue. It holds the versions, the addresses the plugin talks to, the session state, your organizations' names and the last refusals with ALIGN's codes; never a token nor an e-mail address. The plugin sends no telemetry.

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).

Copyright (C) 2026 Geo Nova.
