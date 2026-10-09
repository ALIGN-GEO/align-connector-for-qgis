# ALIGN Connector for QGIS

Publish QGIS layers to your [ALIGN](https://aligngeo.com) organization's Datastore — vector and raster — without leaving QGIS.

> **Status: early development, not yet distributed.** Signing in and publishing vector and raster layers are implemented and proven end to end against a live ALIGN account. Browsing your Datastore from QGIS comes next.

## What it does

You work in QGIS. Your data belongs in ALIGN, where your team consumes it and your web maps are built from it. Today that means exporting a file and uploading it through a browser. This plugin removes that round trip:

1. **Sign in once** — the plugin opens your browser, you approve, and it comes back with a token. No password, no API key to copy, nothing to paste.
2. **Open the ALIGN panel** — tick one layer, several, or the whole project, pick an organization, publish. Each layer becomes its own publication: the panel's Activity view shows every one of them progressing, then what ALIGN made of it, with a link to the item. A layer's context menu offers the same entry for just that layer. Tick “Also create a web map” and, once every layer has landed, ALIGN assembles a web map of them in the Layers panel's order, with their visibility, their style and the view your canvas showed, ready to open in the Map builder. A layer the Map builder cannot show (a mesh, a point cloud, a WMS or XYZ connection, a table without geometry, curved geometries) is listed greyed with the reason, never silently skipped.

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
    "INPUT": "Countries", "ORGANIZATION": "Town GIS", "VISIBILITY": 1, "SELECTED_ONLY": False,
})
print(result["ASSET_ID"], result["ALIGN_URL"])
```

The organization is given by name or id; a wrong one is refused with the list of yours. A service layer is refused with a pointer to the panel, which registers it as a reference.

## Requirements

- QGIS **3.22 LTR or newer**, QGIS 4.x included: one source for Qt5 and Qt6 through the `qgis.PyQt` shim and `qtcompat.qt_enum`. Developed against 3.44 LTR (Qt5, Python 3.12); CI loads the plugin on the `qgis/qgis:ltr` and `qgis/qgis:4.2` images.
- An [ALIGN](https://aligngeo.com) account, and membership of at least one organization with a role that can publish

No third-party Python packages. Nothing to `pip install`.

## Languages

The plugin speaks the language QGIS runs in (*Settings → Options → General*): **English** (the source) and **French**. A language without a translation reads as English. The refusals ALIGN sends back are composed by the plugin from the server's structured answer, so a « budget insuffisant » reads in your language with its numbers.

Adding a language is a translation file, no code: `python scripts/translations.py extract` refreshes `align_connector/i18n/align_connector_<lang>.ts` (open it in Qt Linguist or any editor), `python scripts/translations.py compile` writes the `.qm` the plugin loads, and `python scripts/translations.py check` is what CI runs: every sentence present and translated, the `.qm` up to date. Dutch and German come next, by the same files.

## Installation

Not yet published to the QGIS plugin repository. Until then, clone this repository and copy (or symlink) the `align_connector/` folder into your QGIS profile:

| Platform | Path |
|---|---|
| Windows | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\` |
| macOS | `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/` |
| Linux | `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/` |

Then enable **ALIGN Connector** in *Plugins → Manage and Install Plugins → Installed*.

### Working on the plugin

On Windows, `scripts/dev_install.ps1` links this repository into your QGIS profile, so an edit here is picked up by reloading the plugin — no copying between iterations.

> **Do not use QGIS' "Uninstall" button on a linked install.** It deletes the plugin folder and follows the link, erasing the source files in this repository. To turn the plugin off, *uncheck* it in the plugin manager; to remove it for real, run `scripts/dev_install.ps1 -Uninstall`, which removes the link and never touches what it points at.
>
> If it already happened: `git restore align_connector/` brings everything back, as long as the work was committed.

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
