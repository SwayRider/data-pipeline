# SwayRider Data Pipeline

The data pipeline builds all geodata required by the SwayRider backend: vector tile MBTiles,
routable graphs (Valhalla), geocoding data (Pelias) and border-crossing metadata.

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Configuration](#configuration)
4. [Pipelines](#pipelines)
   - [OSM Data Pipeline](#osm-data-pipeline)
   - [Border Data Pipeline](#border-data-pipeline)
   - [Valhalla Routing Data Pipeline](#valhalla-routing-data-pipeline)
   - [Pelias Geocoding Data Pipeline](#pelias-geocoding-data-pipeline)
   - [Tiles Pipeline](#tiles-pipeline)
5. [Scripts](#scripts)
6. [Tile Layers / MBTiles Schema](#tile-layers--mbtiles-schema)
   - [Highway label extraction](#highway-label-extraction)
7. [Data Sources](#data-sources)
8. [Storage Layout](#storage-layout)
9. [Development Tips](#development-tips)

---

## Overview

Five independent pipelines share the same config and result directory but track progress
in separate manifest files so they can run and be re-run independently.

| Pipeline | Script | Manifest | Output |
|---|---|---|---|
| OSM data | `./build-osm` | `manifest-osm.yml` | `osm.tar.bz2` |
| Border data | `./build-border-data` | `manifest-border.yml` | `border.tar.bz2` |
| Valhalla routing data | `./build-valhalla-data` | `manifest-valhalla.yml` | `valhalla.tar.bz2` |
| Pelias geocoding data | `./build-pelias-data` | `manifest-pelias.yml` | `pelias.tar.bz2` |
| Tiles | `./build-tiles` | `manifest-tiles.yml` | `tiles.tar` |

### Pipeline dependencies

```
build-osm  ──┬──▶  build-border-data
             ├──▶  build-valhalla-data
             └──▶  build-pelias-data

build-tiles  (independent)
```

`build-border-data`, `build-valhalla-data`, and `build-pelias-data` each verify that the
required OSM output files from `build-osm` are present before starting. Run `build-osm` first.

### Recommended execution order

```bash
# Step 1 — always first
./build-osm --config config/config.yml --tag 2026-03-09

# Step 2 — these three can run in parallel or in any order
./build-border-data   --config config/config.yml --tag 2026-03-09
./build-valhalla-data --config config/config.yml --tag 2026-03-09
./build-pelias-data   --config config/config.yml --tag 2026-03-09

# Step 3 — independent, can run at any time
./build-tiles --config config/config.yml --tag 2026-03-09
```

---

## Prerequisites

- Python 3.10+
- `osmium-tool` (CLI)
- `git`, `cmake`, `make` (for compiling GDAL and Tippecanoe)
- `tar`
Python dependencies:

```bash
pip install -r requirements.txt
```

---

## Configuration

Config files live in `config/`:

| File | Purpose |
|---|---|
| `config.yml` | Production config |
| `config-dev.yml` | Local development config |
| `config-test.yml` | CI / test config |

Key config sections:

| Section | Description |
|---|---|
| `max_workers` | Parallelisation degree |
| `build_paths` | Local directories for downloads, tools, temp files and results |
| `download_urls` | Source URLs for OSM, SRTM, Natural Earth, OSM land-polygons |
| `tile_regions` | Large OSM regional files to download for tile extraction (e.g. `europe`) |
| `region_size` | Bounding box (lat/lon) for the entire tile grid |
| `tile_size` | Grid cell size in degrees (default: 10) |
| `regions` | Named regions for main pipeline – OSM files, WOF codes, SRTM bounding boxes |
| `border-regions` | Region pairs used for border-crossing detection |
| `tippecanoe` / `osgeo` / `valhalla` / `pelias` | Tool build configs |
| `natural_earth` | Natural Earth file paths relative to the NE download URL |

> **Note:** `config.yml` may be a symlink to a platform-specific config (e.g. `config-mac.yml` for macOS). All pipelines default to `config/config.yml`.

---

## Pipelines

### OSM Data Pipeline

Produces per-region OSM `.osm.pbf` extracts with overlap regions for routing continuity.

**Output files:**
- OSM `.osm.pbf` files per region (with overlap extracts for routing continuity)
- Core `.osm.pbf` files per region (used by border and Valhalla pipelines)
- `osm.tar.bz2` archive of all OSM files

**Pipeline steps:**

1. Download OSM data (per-region PBFs from Geofabrik + tile regions)
2. Download SRTM elevation tiles
3. Download Natural Earth data
4. Create overlap extracts
5. Build per-region OSM files (merge core + overlap, clip to border)
6. Package to `osm.tar.bz2`

### Border Data Pipeline

Requires: OSM output from `build-osm`. Produces region borders and border-crossing metadata.

**Output files:**
- Region border GeoJSON files
- Border crossing CSV files
- `border.tar.bz2` archive

**Pipeline steps:**

1. Verify prerequisites (OSM files from build-osm)
2. Extract region borders
3. Create region border areas
4. Detect border crossings
5. Package to `border.tar.bz2`

### Valhalla Routing Data Pipeline

Requires: OSM output from `build-osm`. Produces Valhalla routing graph tiles.

**Output files:**
- Valhalla routing graph tiles per region
- `valhalla.tar.bz2` archive

**Pipeline steps:**

1. Compile Valhalla from source (latest release, cached after first run)
2. Verify prerequisites (OSM files from build-osm, SRTM data)
3. Build Valhalla graph tiles
4. Package to `valhalla.tar.bz2`

### Pelias Geocoding Data Pipeline

Requires: OSM output from `build-osm`. Produces Pelias geocoding data.

**Output files:**
- Pelias geocoding data (schema, WhoIsOnFirst, OpenAddresses, OSM, Geonames)
- `pelias.tar.bz2` archive

**Pipeline steps:**

1. Install Pelias npm tools (latest releases, cached after first run)
2. Verify prerequisites (OSM files from build-osm)
3. Download Pelias placeholder data
4. Build Pelias data (schema → import WOF → import addresses → import OSM)
5. Package to `pelias.tar.bz2`

### Tiles Pipeline

Generates vector tile MBTiles from large regional OSM extracts and Natural Earth data:

1. Download regional OSM PBFs (e.g. `europe-latest.osm.pbf`), Natural Earth shapefiles and OSM land-polygons
2. Extract 10° grid tiles from the OSM source using `osmium extract`
3. Filter features by zoom level (L1 filter / L2 filter) using `osmium tags-filter`
4. Export GeoJSON per layer using `osmium export`
5. Clean null/empty properties from road files
6. Extract highway label features (`highway_labels.geojson`) from roads — A/E numbers split into separate features per ref value (see [Highway label extraction](#highway-label-extraction))
7. Filter small polygons (L1 land: area < 0.0008°²; water polygons by area, bbox and connectivity; forest polygons by area and compactness)
8. Stamp `tippecanoe` minzoom hints on water features (L1: 7, L2: 11) and forest features (L1: 7, L2: 11) to prevent pop-in/pop-out
9. Simplify geometries per level/layer with `ogr2ogr`
10. Add `minzoom` properties and convert to Tippecanoe format
11. Build MBTiles per zoom level with `tippecanoe`
12. Archive to `tiles.tar`
13. (Optional) Publish artifacts to output directory

---

## Scripts

### `./build-osm`

Run the OSM data pipeline.

```
./build-osm [--config CONFIG] [--clean] [--tag TAG]
```

| Flag | Description |
|---|---|
| `--config` | Path to config file (default: `config/config.yml`) |
| `--clean` | Clean build directories before running |
| `--tag` | Version tag; defaults to current date `YYYY-MM-DD` |

### `./build-border-data`

Run the border data pipeline.

```
./build-border-data [--config CONFIG] [--clean] [--tag TAG]
```

| Flag | Description |
|---|---|
| `--config` | Path to config file (default: `config/config.yml`) |
| `--clean` | Clean build directories before running |
| `--tag` | Version tag; defaults to current date `YYYY-MM-DD` |

### `./build-valhalla-data`

Run the Valhalla routing data pipeline.

```
./build-valhalla-data [--config CONFIG] [--clean] [--tag TAG]
```

| Flag | Description |
|---|---|
| `--config` | Path to config file (default: `config/config.yml`) |
| `--clean` | Clean build directories before running |
| `--tag` | Version tag; defaults to current date `YYYY-MM-DD` |

### `./build-pelias-data`

Run the Pelias geocoding data pipeline.

```
./build-pelias-data [--config CONFIG] [--clean] [--tag TAG]
```

| Flag | Description |
|---|---|
| `--config` | Path to config file (default: `config/config.yml`) |
| `--clean` | Clean build directories before running |
| `--tag` | Version tag; defaults to current date `YYYY-MM-DD` |

### `./build-tiles`

Run the tiles pipeline.

```
./build-tiles [--config CONFIG] [--clean] [--tag TAG]
              [--skip-download] [--skip-build] [--upload]
              [--no-parallel] [--gen-tile TILE_ID]
              [--with-service-roads]
```

| Flag | Description |
|---|---|
| `--config` | Path to config file (default: `config/config.yml`) |
| `--clean` | Clean build directories before running |
| `--tag` | Version tag; defaults to current date `YYYY-MM-DD` |
| `--skip-download` | Skip downloading OSM/Natural Earth data |
| `--skip-build` | Skip building tiles |
| `--upload` | Deprecated: no effect |
| `--no-parallel` | Disable parallelisation (easier debugging) |
| `--gen-tile` | Build only a single tile, e.g. `N50_E000` |
| `--with-service-roads` | Include service roads (parking, driveways) in L2 |

### `./publish`

Publish already-built artifacts to the geodata output directory.

```
./publish [--config CONFIG] [--manifest FILENAME]
```

| Flag | Description |
|---|---|
| `--config` | Path to config file (default: `config/config.yml`) |
| `--manifest` | Manifest file to publish (default: `manifest-data.yml`) |

### Publish examples

```bash
# Build and publish OSM data
./build-osm --config config/config.yml --tag 2026-03-09
./publish   --config config/config.yml --manifest manifest-osm.yml

# Build and publish border data
./build-border-data --config config/config.yml --tag 2026-03-09
./publish           --config config/config.yml --manifest manifest-border.yml

# Build and publish tiles
./build-tiles --config config/config.yml --tag 2026-03-09
./publish     --config config/config.yml --manifest manifest-tiles.yml

# Publish only tiles (without rebuilding)
./publish --config config/config.yml --manifest manifest-tiles.yml
```

---

## Tile Layers / MBTiles Schema

Tiles are split into three MBTiles files per 10° grid cell, covering different zoom ranges.

### L0 (`L0.mbtiles`) — Z0–6, global overview

Source: Natural Earth

| Layer | Geometry | Attributes | Notes |
|---|---|---|---|
| `land` | polygon | — | `ne_10m_land` |
| `ocean` | polygon | — | `ne_10m_ocean` |
| `urban` | polygon | — | `ne_10m_urban_areas` |
| `boundaries` | polygon | `name_en`, `name_nl`, `name_de`, `name_fr`, `name_es`, `name_pt`, `name_ru`, `name_ar`, `name_zhs`, `name_zht`, `name_ja`, `name_ko`, `name_hi`, `name_it`, `name_pl`, `name_sv`, `name_tr`, `name_el`, `name_bn`, `name_fa`, `name_he`, `name_uk`, `name_ur`, `name_vi`, `name_hu`, `name_id`, `minzoom` | `ne_10m_admin_0_countries`; field names lowercased |
| `country_labels` | point | same language fields + `minzoom` | ST_PointOnSurface of country polygons; `minzoom` derived from country area |
| `roads` | linestring | `highway`, `is_ferry`, `minzoom` | NE major highways + expressways; ferry routes tagged |

### L1 (`L1/{tile}.mbtiles`) — Z7–10, coarse detail

Source: OSM (osmium export + land-polygons shapefile)

| Layer | Geometry | Attributes | Notes |
|---|---|---|---|
| `land` | polygon | — | OSM land-polygons clipped to tile; small polygons (< 0.0008°²) filtered |
| `water` | polygon | `natural`, `landuse` | OSM water areas; filtered by area (< 0.0008°²), bbox and connectivity; tippecanoe minzoom=7 |
| `urban` | polygon | — | Raw OSM `landuse=residential/commercial/industrial` polygons exported via osmium; simplified (0.0015°) |
| `forest` | polygon | `natural`, `landuse` | OSM woodland/forest; filtered by area (< 0.001°²) and Polsby-Popper compactness (< 0.05); simplified (0.0015°) |
| `roads` | linestring | `highway`, `name`, `ref`, `minzoom`, `tunnel`, `bridge`, `motorway_link_type`, `route` | Simplified (0.0007°) |
| `railways` | linestring | `railway`, `name`, `minzoom` | Simplified (0.0007°) |
| `ferries` | linestring | `route`, `minzoom` | Simplified (0.0007°) |
| `highway_labels` | linestring | `ref`, `ref_type` | Motorway/trunk A/E shields + national road N shields; one feature per ref value; `ref_type`: `A`, `E`, or `N`; see [Highway label extraction](#highway-label-extraction) |
| `places` | point | `place`, `name`, `population`, `capital`, `admin_level`, `minzoom` | City/town/village nodes; `minzoom` computed from place type and population |

### L2 (`L2/{tile}.mbtiles`) — Z11–16, full detail

Source: OSM (osmium export + land-polygons shapefile)

| Layer | Geometry | Attributes | Notes |
|---|---|---|---|
| `land` | polygon | — | OSM land-polygons clipped to tile |
| `water` | polygon | `natural`, `landuse` | OSM water areas; filtered by area (< 0.00008°²); tippecanoe minzoom=11; simplified (0.0001°) |
| `urban` | polygon | — | Raw OSM `landuse=residential/commercial/industrial` polygons exported via osmium; simplified (0.0001°) |
| `forest` | polygon | `natural`, `landuse` | OSM woodland/forest; filtered by area (< 0.0001°²) and Polsby-Popper compactness (< 0.03); tippecanoe minzoom=11; simplified (0.0001°) |
| `roads` | linestring | `highway`, `name`, `ref`, `minzoom`, `tunnel`, `bridge`, `motorway_link_type`, `route` | No simplification |
| `railways` | linestring | `railway`, `name`, `minzoom` | No simplification |
| `ferries` | linestring | `route`, `minzoom` | No simplification |
| `waterways` | linestring | `waterway`, `name`, `width` | Rivers, canals (linestrings only) |
| `highway_labels` | linestring | `ref`, `ref_type` | Motorway/trunk A/E shields + national road N shields; one feature per ref value; `ref_type`: `A`, `E`, or `N`; see [Highway label extraction](#highway-label-extraction) |
| `places` | point | `place`, `name`, `population`, `capital`, `admin_level`, `minzoom` | City/town/village/suburb nodes |

### Simplification tolerances

| Level | Roads / Railways | Polygons |
|---|---|---|
| L0 | 0.01° (~1.1 km) | 0.1° (~11 km) |
| L1 | 0.0007° (~77 m) | 0.0015° (~167 m) |
| L2 | none | 0.0001° (~11 m, ~2–4 tile units at Z11–12) |

### `minzoom` logic for places

The pipeline sets a `minzoom` property on each place feature (used by Tippecanoe to drop
features below their minimum zoom level in the tile):

| `place` value | `minzoom` |
|---|---|
| `city` | 7 |
| `town` | 9 (8 if population > 50 000) |
| `village` | 11 |
| `suburb` | 12 |
| other | 13 |

The map style applies an additional layer of graduated rendering on top of these tile-level
thresholds, using the exported `population` field to split cities:

| Place type | Style layer visible from | Condition |
|------------|--------------------------|-----------|
| Capitals | Z7 | `capital=yes` or `capital=2` |
| `city` | Z8 | population ≥ 25 000, unknown, or not set |
| `city` | Z9 | population known and < 25 000 |
| `town` | Z10 | — |
| `village` | Z12 | — |
| `hamlet`, `suburb` | Z14 | — |

> The `population` OSM tag is exported as a string. Missing or empty values fall back to the Z8
> (major city) tier to avoid hiding real cities that lack population data.

### Water polygon filtering

After simplification, water polygons are filtered in two stages before being packed into MBTiles.

**Stage 1 — area / connectivity filter** (applied to L1 and L2):

Certain `water` tag values indicate flowing or linear water and are always kept regardless of size:
`river`, `canal`, `stream`, `oxbow`, `tidal_channel`, `moat`.

All other water bodies (lakes, ponds, reservoirs, lagoons, basins, etc.) must pass at least one
of the following criteria to be retained:

| Criterion | L1 threshold | L2 threshold |
|---|---|---|
| Area (grouped by name) | ≥ 0.0008°² (~10 km²) | ≥ 0.00008°² (~1 km²) |
| Bounding-box longest dimension | ≥ 0.3° (~33 km) | ≥ 0.3° (~33 km) |
| Connected component of a kept body | yes | yes |

The bounding-box check preserves elongated rivers/canals whose individual OSM polygons may be
small but span a significant fraction of a tile. The connectivity check keeps OSM polygon
components that form part of a named water body that would otherwise pass the area test.

**Stage 2 — tippecanoe minzoom hints** (applied after Stage 1):

Every surviving water feature receives a `tippecanoe` object with a `minzoom` key:

| Level | Zoom range | `tippecanoe.minzoom` |
|---|---|---|
| L1 | Z7–10 | 7 |
| L2 | Z11–16 | 11 |

Without this hint, tippecanoe's `--drop-densest-as-needed` algorithm makes independent
per-tile decisions that are non-monotonic at tile boundaries — a polygon that fits inside a
single Z8 tile is split across four Z9 tiles, and each clipped piece may fall below the drop
threshold, causing water bodies to appear at Z8 but disappear at Z9. The minzoom hint forces
tippecanoe to include the feature from the specified zoom level onwards.

### Forest polygon filtering

Forest polygons (L1 and L2) are filtered by combined area and Polsby-Popper compactness before simplification (STEP 2.3 in `process_single_tile()`). This removes sub-pixel woodland fragments and narrow elongated strips (road-side tree rows, hedgerow remnants) that are invisible at the target zoom but inflate tile size.

**Polsby-Popper score** = 4π × area / perimeter²  (0–1, where 1 = perfect circle).

Perimeter is computed via `ST_Length(ST_Boundary(geometry))` — portable across both `Polygon` and `MultiPolygon` geometries in SpatiaLite.

| Level | Area threshold | Compactness threshold | Rationale |
|---|---|---|---|
| L1 | 0.001°² (~100 km²) | 0.05 (≈ 1:50 aspect ratio) | Removes tiny woodland patches invisible at Z7–10 |
| L2 | 0.0001°² (~10 km²) | 0.03 (≈ 1:100 aspect ratio) | Removes sub-km² fragments and narrow strips at Z11–16 |

A polygon passes the filter only if it satisfies **both** thresholds. Large, compact forest areas are always retained; only fragments and strips are dropped.

| Shape example | PP score | Outcome at L2 |
|---|---|---|
| Large compact woodland | ~0.5 | Kept |
| Irregular forest patch ~1 km² | ~0.1–0.4 | Kept |
| Road-side tree row (1:200 strip) | ~0.01 | Removed |
| Tiny woodland fragment < 0.05 km² | any | Removed |

Filtering runs on unsimplified geometry (before STEP 3), so area and shape measurements are accurate.

**Stage 2 — tippecanoe minzoom hints** (STEP 2.4, applied after Stage 1):

Every surviving forest feature receives a `tippecanoe` object with a `minzoom` key. This mirrors the water minzoom mechanism and prevents tippecanoe from silently dropping forest polygons at tile boundaries due to per-tile size limits. Without this hint, a tile covering a dense 10° cell fits within tippecanoe's byte/feature budget, but the subtiles at the next zoom may each exceed it independently — causing forest to appear at one zoom and disappear at the next (pop-out).

| Level | `tippecanoe.minzoom` |
|---|---|
| L1 | 7 |
| L2 | 11 |

### Highway label extraction

The `highway_labels` layer is derived from `roads.geojson` during pipeline Step 2.5. It provides
the data for colored shield labels (blue A/E-numbers, blue N-numbers) rendered along motorway,
trunk, primary, secondary and tertiary roads in the map style.

**Extraction logic** (`process_file_highway_labels` in `tiles.py`):

Two separate loops write into the same `output_features` list.

**Loop 1 — motorway/trunk shields (A/E):**

1. Only features with `highway=motorway` or `highway=trunk` are considered.
2. The `ref` tag is split on `";"` and each piece is stripped of whitespace.
3. Pieces matching the pattern `^(AP|DE|[AEMDRSaemdrs])\s*[-]?\s*\d+$` are extracted.
4. Each matching piece produces a **separate** output feature with the same geometry as the road:
   - `ref` — uppercased ref value (e.g. `"A1"`, `"E40"`, `"S8"`)
   - `ref_type` — `"E"` if ref starts with `E`, otherwise `"A"` (blue shield)
   - No `tippecanoe.minzoom` override; features appear from Z7 (MBTiles range floor).

**Loop 2 — national road shields (N):**

1. Features with `highway` in `trunk`, `primary`, `secondary`, `tertiary` are considered.
2. The `ref` tag is split on `";"` and each piece is stripped of whitespace.
3. Pieces matching the pattern `^(SS|SP|DN|DJ|DK|DW|Rv|Fv|[NB])\s*[-]?\s*\d+$` (case-insensitive) are extracted.
   - `D` + num is deliberately excluded here; it is already handled by Loop 1 (Czech/Slovak `D1`, Croatian `D1`).
4. Each matching piece produces a **separate** output feature:
   - `ref` — uppercased and space-stripped ref value (e.g. `"N7"`, `"SS1"`, `"DK1"`)
   - `ref_type` — `"N"` for all national roads (same blue shield style)
   - `tippecanoe.minzoom` — derived from highway class (see table below)

| `highway` value | `tippecanoe.minzoom` |
|---|---|
| `trunk` | 10 |
| `primary` | 10 |
| `secondary` | 11 |
| `tertiary` | 12 |

A road carrying `ref="A1;E40"` produces two features with identical geometry. MapLibre's
line-symbol collision detection then naturally alternates A and E shields along the road.

**Covered ref formats by country:**

*Motorway/trunk (Loop 1):*

| Prefix | Example | Countries |
|--------|---------|-----------|
| `A` + num | `A1`, `A 3` | Germany, Austria, Switzerland, Italy, France, Portugal, Romania, Bulgaria, Croatia, Slovenia, Serbia, Netherlands, Belgium, UK (trunk) |
| `E` + num | `E40` | European routes (all countries) |
| `M` + num | `M1` | UK, Ireland, Hungary |
| `D` + num | `D1` | Czech Republic, Slovakia, Croatia |
| `AP-` + num | `AP-7` | Spain (toll motorways) |
| `A-` + num | `A-1` | Spain (free motorways) |
| `S` + num | `S3`, `S8` | Poland (expressways) |
| `R` + num | `R61`, `R67` | Hungary (expressways) |
| `DE` + num | `DE1` | Romania (expressways) |

*National roads (Loop 2):*

| Prefix | Example | Countries / road class |
|--------|---------|------------------------|
| `N` + num | `N7`, `N20` | France, Belgium, Netherlands, Portugal, Norway, Serbia — trunk/primary |
| `B` + num | `B1`, `B9` | Germany (Bundesstraße), Austria, UK (B-roads) — trunk/primary/secondary |
| `SS` + num | `SS1` | Italy (Strada Statale) — primary |
| `SP` + num | `SP12` | Italy (Strada Provinciale) — secondary |
| `DN` + num | `DN1` | Romania (Drum Național) — primary |
| `DJ` + num | `DJ100` | Romania (Drum Județean) — secondary |
| `DK` + num | `DK1` | Poland (Droga Krajowa) — primary |
| `DW` + num | `DW200` | Poland (Droga Wojewódzka) — secondary |
| `Rv` + num | `Rv4` | Norway (Riksvei) — trunk/primary |
| `Fv` + num | `Fv17` | Norway (Fylkesvei) — secondary |

**Map style layers** (`assets/map/styles/light.json`):

| Layer ID | `ref_type` filter | Text background | Visible from |
|---|---|---|---|
| `highway-shields-n` | `N` | `#003da5` (blue) | Z10 |
| `highway-shields-a` | `A` | `#003da5` (blue) | Z7 |
| `highway-shields-e` | `E` | `#007a3d` (green) | Z7 |

`highway-shields-n` is rendered **before** `highway-shields-a` in the layer order so that
motorway shields win collision resolution (later layers take priority in MapLibre).

A/E layers use `symbol-placement: line` with spacing that decreases from 900 px at Z7 to 200 px
at Z14. The N layer uses spacing from 800 px at Z10 to 200 px at Z16. Text size grows proportionally
with zoom for all three layers.

### Urban layer source

**L0** uses Natural Earth `ne_10m_urban_areas` clipped to the tile bbox via `ogr2ogr -clipsrc`.

**L1 and L2** use a raw osmium export: `urban.pbf → urban.geojson` via `osmium export` with
`config/osmium-export-urban.json`. The export selects polygon and multipolygon geometries with
`landuse=residential`, `landuse=commercial`, or `landuse=industrial`. No post-processing is
applied; the raw OSM polygons are written directly to `urban.geojson` and then simplified
with the rest of the layer stack.

No `landuse` attribute is preserved in the output. All urban areas render as a single class;
map styles must not filter the urban layer by `landuse`.

---

## Data Sources

| Source | Used for |
|---|---|
| [Geofabrik](https://download.geofabrik.de/) | Regional OSM PBF files (main pipeline + tile pipeline) |
| [OSM land-polygons-split-4326](https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip) | Land layer (L1, L2) |
| [Natural Earth](https://naturalearth.s3.amazonaws.com/) | L0 land, ocean, country boundaries, roads, urban layer |
| [SRTM (AWS elevation tiles)](s3://elevation-tiles-prod/skadi/) | Elevation contours |
| [Pelias placeholder](https://data.geocode.earth/placeholder/) | Geocoding placeholder store |
| WhoIsOnFirst (WOF) | Administrative boundaries for geocoding |
| OpenAddresses | Address data per country |

---

## Storage Layout

```
result_dir/
├── manifest-osm.yml              # OSM pipeline manifest
├── manifest-border.yml           # Border pipeline manifest
├── manifest-valhalla.yml         # Valhalla pipeline manifest
├── manifest-pelias.yml           # Pelias pipeline manifest
├── manifest-tiles.yml            # Tiles pipeline manifest
├── osm/
│   ├── {region}.osm.pbf          # Per-region OSM extracts
│   └── {region}-core.osm.pbf    # Per-region core OSM (used by border pipeline)
├── borders/
│   ├── {region}-core.geojson
│   └── {region}-extended.geojson
├── border-crossings/
│   └── {border-pair}.csv
├── valhalla/
│   └── {region}/
│       ├── tiles.tar
│       ├── admin.sqlite
│       └── tz_world.sqlite
├── pelias/
│   ├── placeholder/
│   ├── config/{region}/pelias.json
│   └── wof/{region}/
├── tiles/
│   ├── L0.mbtiles
│   ├── L1/{TILE}.mbtiles
│   └── L2/{TILE}.mbtiles
├── osm.tar.bz2
├── border.tar.bz2
├── valhalla.tar.bz2
├── pelias.tar.bz2
└── tiles.tar
```

Output paths (after `./publish`):

```
geodata/
├── data/{tag}/
│   ├── osm/{region}.osm.pbf
│   ├── borders/{region}-core.geojson
│   ├── borders/{region}-extended.geojson
│   ├── border-crossings/{border-pair}.csv
│   ├── valhalla/{region}/tiles.tar
│   ├── pelias/placeholder/{file}
│   ├── pelias/config/{region}/pelias.json
│   ├── pelias/wof/{region}/{file}
│   ├── manifest-osm.yml
│   ├── manifest-border.yml
│   ├── manifest-valhalla.yml
│   └── manifest-pelias.yml
└── tiles/{tag}/
    ├── tiles.tar
    └── manifest-tiles.yml
```

---

## Development Tips

- Use `--gen-tile N50_E000` to build only one grid cell while iterating on tile logic.
- Use `--skip-download` to re-run without re-downloading large files.
- `osmium-export-*.json` files in `config/` control which OSM tags are exported per layer.
- Tile IDs follow the format `{N|S}{lat:02d}_{E|W}{lon:03d}` (e.g. `N50_E000`, `S10_W040`).
- The pipeline stops immediately on the first tile error; fix the failing tile and re-run with `--gen-tile`.
