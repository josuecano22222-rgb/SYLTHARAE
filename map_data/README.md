# Offline map packages

Compressed map databases for the **Map Viewer** live here.

## Layout

```
map_data/
  catalog.json          # Layer + package catalog (online + offline entries)
  registry.db           # SQLite registry (portable metadata mirror, auto-synced)
  packages/             # Downloaded .tar.gz / .zip archives
  installed/<id>/       # Extracted packages (manifest + tiles)
  runtime/              # Download progress and staging (automatic)
```

Auto-captured map tiles (while browsing online) are stored as **compressed WebP
BLOBs** in `registry.db` → table `tile_cache`, not as loose PNG files.
Legacy `cache/` folders are imported into the database automatically when detected.

**Portable bundle:** In Map Viewer use **Export portable map bundle** to download
`*.sylmap.tar.gz` (catalog + registry + packages + installed tiles). Extract into
the project `map_data/` folder on another machine running the same app version.

## Package archive format

Each downloadable archive must unpack to a folder containing:

- `manifest.json` — required metadata
- Either vector **`basemap.pmtiles`** *or* raster tiles at `tiles/{z}/{x}/{y}.png`

Example `manifest.json`:

```json
{
  "name": "My region",
  "pmtiles": "basemap.pmtiles",
  "bounds": [-125.0, 24.0, -66.0, 49.0],
  "center": [-98.0, 39.0, 4],
  "max_zoom": 14,
  "attribution": "© Your data source"
}
```

For raster-only packs:

```json
{
  "name": "Raster excerpt",
  "raster_template": "tiles/{z}/{x}/{y}.png",
  "bounds": [-74.05, 40.68, -73.95, 40.78],
  "max_zoom": 16,
  "attribution": "© OpenStreetMap contributors"
}
```

## Adding a mirror

Edit `catalog.json` and set `download_url` on a package entry to an HTTPS URL
serving your `.tar.gz`. Use **Map Viewer → Map packages → Download** in the app.

## Manual install

1. Extract the archive into `map_data/installed/<package-id>/`
2. Ensure `manifest.json` is present
3. Refresh the Map Viewer — the layer appears under **Installed**
