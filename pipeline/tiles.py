import os
import shutil
import statistics
import subprocess
import time
from pathlib import Path
import geopandas as gpd


def parse_tile_id(tile_id: str) -> tuple[int, int, int]:
    """Parse tile ID like 'N50_E000' into (lat, lon, tile_size).

    Args:
        tile_id: Tile identifier (e.g., 'N50_E000', 'S10_W040')

    Returns:
        Tuple of (latitude, longitude, tile_size)
        Tile covers [lat, lat+tile_size) x [lon, lon+tile_size)

    Raises:
        ValueError: If tile_id format is invalid

    Examples:
        'N50_E000' -> (50, 0, 10)    # 50°N-60°N, 0°E-10°E
        'N40_W010' -> (40, -10, 10)  # 40°N-50°N, 10°W-0°W
        'S10_E020' -> (-10, 20, 10)  # 10°S-0°S, 20°E-30°E
    """
    try:
        lat_part, lon_part = tile_id.split('_')

        # Parse latitude: N50 or S10
        lat_dir = lat_part[0]  # 'N' or 'S'
        lat_val = int(lat_part[1:])  # 50, 10, etc.
        lat = lat_val if lat_dir == 'N' else -lat_val

        # Parse longitude: E000 or W040
        lon_dir = lon_part[0]  # 'E' or 'W'
        lon_val = int(lon_part[1:])  # 000, 040, etc.
        lon = lon_val if lon_dir == 'E' else -lon_val

        # Tile size is always 10 degrees
        tile_size = 10

        return (lat, lon, tile_size)

    except (ValueError, IndexError) as e:
        raise ValueError(f"Invalid tile_id format '{tile_id}'. Expected format: N50_E000, S10_W040, etc.") from e


# Simplification tolerances in degrees (WGS84 geographic coordinates)
# Based on vector tile coordinate system: 4096 units per tile edge
# Values keep simplified geometry within 2-4 tile units of original
SIMPLIFICATION_TOLERANCES = {
    'L0': {  # Z0-6: Global overview
        'roads': 0.01,         # ≈1.1km at equator (very aggressive for Z4-6)
        'railways': 0.01,
        'ferries': 0.01,
        'polygons': 0.1,       # ≈11km at equator (land/ocean)
    },
    'L1': {  # Z7-10: Coarse overview
        'roads': 0.0007,       # ≈77m at equator
        'railways': 0.0007,
        'ferries': 0.0007,
        'borders': 0.0007,     # ≈77m at equator — same as roads/railways
        'polygons': 0.0015,    # ≈167m at equator
    },
    'L2': {  # Z11-16: Full detail, no road simplification
        'roads': 0,            # NO simplification (merged L3 into L2)
        'railways': 0,
        'ferries': 0,
        'borders': 0,          # NO simplification — full OSM precision
        'polygons': 0.0001,    # ≈11m at equator (~2–4 tile units at Z11–12)
    },
}

# Area thresholds in deg² for water-polygon filtering (equator baseline: 1 km² ≈ 0.00008 deg²)
# L1 (z7-10): large lakes only   — ~10 km²
# L2 (z11-16): medium lakes+     — ~1 km²
WATER_AREA_THRESHOLDS = {
    'L1': 0.0008,    # ~10 km²
    'L2': 0.00008,   # ~1 km²
}

# Area thresholds in deg² for land-polygon filtering after L1 simplification.
# At 0.0015° tolerance many small islands/features collapse to zero-area;
# this removes them before tippecanoe to keep L1 tiles clean.
# L2 tolerances are fine enough that no filtering is needed at higher zoom levels.
LAND_AREA_THRESHOLDS = {
    'L1': 0.0008,    # ~10 km² — matches water L1 threshold
}

FOREST_AREA_THRESHOLDS = {
        #'L1': 0.001,    # ~100 km² — removes tiny woodland patches at Z7-10
        #'L2': 0.0001,   # ~10 km² — removes sub-km² forest fragments at Z11-16
    'L1': 0.0002,    # ~2.5 km²
    'L2': 0.00002,   # ~0.25 km²
}

FOREST_COMPACTNESS_THRESHOLDS = {
    'L1': 0.05,   # PP: rejects strips narrower than ~1:50 aspect ratio
    'L2': 0.03,   # PP: rejects strips narrower than ~1:100 aspect ratio
}

# OSM `water` tag values that indicate flowing/linear water — always kept regardless of area.
# Everything else (lake, pond, reservoir, lagoon, basin, etc.) is subject to area filtering.
WATER_KEEP_TYPES = frozenset({
    'river', 'canal', 'stream', 'oxbow', 'tidal_channel', 'moat',
})

WATER_BBOX_MIN_DIM = 0.3   # ~33 km — preserve rivers crossing a significant fraction of a tile

WATER_MINZOOM = {
    'L1': 7,
    'L2': 11,
}

FOREST_MINZOOM = {
    'L2': 11,
}

def extract_L1_L2_features(
        source_path: str,
        min_lat: int,
        max_lat: int,
        min_lon: int,
        max_lon: int,
        tile_size: int,
        osm_land_shp: str,
        ne_urban_path: str = None,
        gen_tile: str = None) -> bool:

    # Build list of tiles to process
    tiles = []
    for lat in range(min_lat, max_lat, tile_size):
        #lat2 = lat + tile_size
        lat_id = f"S{-lat:02d}" if lat < 0 else f"N{lat:02d}"
        for lon in range(min_lon, max_lon, tile_size):
            #lon2 = lon + tile_size
            lon_id = f"W{-lon:03d}" if lon < 0 else f"E{lon:03d}"
            tile_id = f"{lat_id}_{lon_id}"

            tiles.append((source_path, osm_land_shp, ne_urban_path, lat, lon, tile_size, lat_id, lon_id, tile_id))

    # Filter to single tile if requested
    if gen_tile:
        try:
            target_lat, target_lon, _ = parse_tile_id(gen_tile)
            original_count = len(tiles)
            tiles = [t for t in tiles if t[3] == target_lat and t[4] == target_lon]

            if len(tiles) == 0:
                print(f"ERROR: Requested tile '{gen_tile}' not found in region")
                print(f"       Valid range: lat {min_lat}-{max_lat}, lon {min_lon}-{max_lon}")
                return False

            print(f"\n{'='*80}")
            print(f"TEST MODE: Processing only tile {gen_tile}")
            print(f"Skipping {original_count - len(tiles)} other tiles")
            print(f"{'='*80}\n")

        except ValueError as e:
            print(f"ERROR: {e}")
            return False

    # Process tiles sequentially
    print(f"\n{'='*80}")
    print(f"Processing {len(tiles)} tiles sequentially")
    print("Pipeline will STOP IMMEDIATELY on first error")
    print(f"{'='*80}\n")

    STEP_LABELS = [
        ("step_1",   "Step  1: Export"),
        ("step_2",   "Step  2: Clean road properties"),
        ("step_2_5", "Step  2.5: Highway labels"),
        ("step_3",   "Step  3: Urban post-processing"),
        ("step_4",   "Step  4: Minzoom → places"),
        ("step_5",   "Step  5: Filter water"),
        ("step_6",   "Step  6: Filter land"),
        ("step_7",   "Step  7: Filter forest"),
        ("step_8",   "Step  8: Simplify"),
        ("step_9",   "Step  9: Minzoom hints → water"),
        ("step_10",  "Step 10: Minzoom hints → forest"),
        ("step_11",  "Step 11: Tag motorway_link"),
    ]

    def _fmt_time(secs: float) -> str:
        if secs < 60:
            return f"{secs:.1f}s"
        m = int(secs // 60)
        s = secs - m * 60
        return f"{m}m {s:.0f}s"

    results = []
    all_step_times: list[dict] = []
    for i, tile_args in enumerate(tiles):
        tile_id = tile_args[8]
        print(f"\n--- Processing tile {i+1}/{len(tiles)}: {tile_id} ---")
        result = process_single_tile(tile_args)
        results.append(result)

        if len(result) == 4:
            all_step_times.append(result[3])

        # STOP IMMEDIATELY on first failure
        if not result[1]:  # result is (tile_id, success, error_message, step_times)
            print(f"\n{'='*80}")
            print(f"ERROR: Pipeline stopped at tile {result[0]}")
            print(f"Reason: {result[2]}")
            print(f"{'='*80}\n")
            return False

        print(f"✓ Tile {tile_id} completed successfully")

    print(f"\nSuccessfully processed {len(results)} tiles")

    # Print timing summary
    if all_step_times:
        print(f"\n{'='*80}")
        if gen_tile:
            # Single-tile mode: print total-only table
            print("Step timing summary:")
            totals = all_step_times[0]
            for key, label in STEP_LABELS:
                if key in totals:
                    print(f"  {label:<32} {_fmt_time(totals[key])}")
        else:
            # Multi-tile mode: print full stats table
            col_w = 10
            label_w = 34
            header = (f"{'Step':<{label_w}} {'Min':>{col_w}} {'Max':>{col_w}}"
                      f" {'Mean':>{col_w}} {'Median':>{col_w}} {'Total':>{col_w}}")
            sep = "─" * len(header)
            print(f"Step timing summary:")
            print(sep)
            print(header)
            print(sep)
            for key, label in STEP_LABELS:
                vals = [d[key] for d in all_step_times if key in d]
                if not vals:
                    continue
                row = (f"{label:<{label_w}}"
                       f" {_fmt_time(min(vals)):>{col_w}}"
                       f" {_fmt_time(max(vals)):>{col_w}}"
                       f" {_fmt_time(statistics.mean(vals)):>{col_w}}"
                       f" {_fmt_time(statistics.median(vals)):>{col_w}}"
                       f" {_fmt_time(sum(vals)):>{col_w}}")
                print(row)
            print(sep)

    return True


def _build_simplify_tasks(ops):
    """Build list of (path, level, file_type) for files that need simplification."""
    tasks = []
    for fo in ops.keys():
        if fo.endswith("_strip"):
            continue

        if "/L0/" in fo:
            level = 'L0'
        elif "/L1/" in fo:
            level = 'L1'
        elif "/L2/" in fo:
            level = 'L2'
        else:
            continue

        if fo.endswith("roads.geojson"):
            file_type = 'roads'
        elif fo.endswith("railways.geojson"):
            file_type = 'railways'
        elif fo.endswith("borders.geojson"):
            file_type = 'borders'
        elif fo.endswith("waterways.geojson"):
            continue  # waterways: line geometry, simplification not applicable
        # water, urban, forest polygons: fall through to polygons case
        else:
            file_type = 'polygons'

        tasks.append((fo, level, file_type))
    return tasks


def _build_zorder_tasks(ops):
    """Build list of (path, zorder_type, level) for files that need z_order and minzoom."""
    tasks = []
    for fo in ops.keys():
        if fo.endswith("_strip"):
            continue

        # Determine level
        if "/L0/" in fo:
            level = 'L0'
        elif "/L1/" in fo:
            level = 'L1'
        elif "/L2/" in fo:
            level = 'L2'
        else:
            level = None

        if fo.endswith("roads.geojson") and level:
            zorder_type = 'roads'
            tasks.append((fo, zorder_type, level))  # Pass level for minzoom
        elif fo.endswith("railways.geojson") and level:
            zorder_type = 'railways'
            tasks.append((fo, zorder_type, level))
        elif fo.endswith("waterways.geojson"):
            zorder_type = 'waterways'
            tasks.append((fo, zorder_type))
        elif fo.endswith("urban.geojson"):
            zorder_type = 'urban'
            tasks.append((fo, zorder_type))
        elif fo.endswith("forest.geojson"):
            zorder_type = 'forest'
            tasks.append((fo, zorder_type))
        else:
            zorder_type = 'background'
            tasks.append((fo, zorder_type))

    return tasks


def _build_water_filter_tasks(ops):
    """Build list of water-polygon files that need area filtering."""
    tasks = []
    for fo in ops.keys():
        if fo.endswith("_strip"):
            continue
        if "/L1/" in fo and fo.endswith("water.geojson"):
            tasks.append((fo, WATER_AREA_THRESHOLDS['L1']))
        elif "/L2/" in fo and fo.endswith("water.geojson"):
            tasks.append((fo, WATER_AREA_THRESHOLDS['L2']))
    return tasks


def _build_land_filter_tasks(ops):
    """Build list of land-polygon files that need area filtering.

    Filters L1 land before simplification to remove features too small to
    display (~10 km²). L2 tolerances are fine enough that no pre-filter is needed.
    """
    tasks = []
    for fo in ops.keys():
        if fo.endswith("_strip"):
            continue
        if "/L1/" in fo and fo.endswith("land.geojson"):
            tasks.append((fo, LAND_AREA_THRESHOLDS['L1']))
    return tasks


def _build_forest_filter_tasks(ops):
    """Build list of (geojson_path, area_threshold, compactness_threshold) for forest filtering.

    Both L1 and L2 forest are active. L0 has no forest layer (uses Natural Earth land data).
    """
    tasks = []
    for fo in ops.keys():
        if fo.endswith("_strip"):
            continue
        if "/L1/" in fo and fo.endswith("forest.geojson"):
            tasks.append((fo, FOREST_AREA_THRESHOLDS['L1'], FOREST_COMPACTNESS_THRESHOLDS['L1']))
        elif "/L2/" in fo and fo.endswith("forest.geojson"):
            tasks.append((fo, FOREST_AREA_THRESHOLDS['L2'], FOREST_COMPACTNESS_THRESHOLDS['L2']))
    return tasks



def process_single_tile(args):
    """Process a single tile with all its layers (L1, L2).

    Args:
        args: Tuple of (source_path, osm_land_shp, lat, lon, tile_size, lat_id, lon_id, tile_id)

    Returns:
        Tuple of (tile_id, success, error_message, step_times)
    """
    source_path, osm_land_shp, ne_urban_path, lat, lon, tile_size, lat_id, lon_id, tile_id = args
    _step_times: dict[str, float] = {}
    lat2 = lat + tile_size
    lon2 = lon + tile_size

    # Get config directory for osmium export configs (relative to this script)
    # Script is at: .../data-pipeline/pipeline/tiles.py
    # Config is at:  .../data-pipeline/config/*.json
    script_path = Path(__file__).resolve()
    config_dir = script_path.parent.parent / "config"
    roads_config = str(config_dir / "osmium-export-roads.json")
    railways_config = str(config_dir / "osmium-export-railways.json")
    ferries_config = str(config_dir / "osmium-export-ferries.json")
    waterways_config = str(config_dir / "osmium-export-waterways.json")
    water_config = str(config_dir / "osmium-export-water.json")
    forest_config = str(config_dir / "osmium-export-forest.json")
    places_config = str(config_dir / "osmium-export-places.json")
    urban_config = str(config_dir / "osmium-export-urban.json")
    borders_config = str(config_dir / "osmium-export-borders.json")

    try:
        l1_base_path = os.path.join(source_path, tile_id, "L1")
        l2_base_path = os.path.join(source_path, tile_id, "L2")

        source_l1_path = os.path.join(l1_base_path, "filtered")
        source_l2_path = os.path.join(l2_base_path, "filtered")

        dest_l1_path = os.path.join(l1_base_path, "geojson")
        dest_l2_path = os.path.join(l2_base_path, "geojson")

        os.makedirs(dest_l1_path, exist_ok=True)
        os.makedirs(dest_l2_path, exist_ok=True)

        ops = {}

        # L1 - Land (OSM land polygons)
        fo = os.path.join(dest_l1_path, "land.geojson")
        ops[fo] = f"ogr2ogr -of GeoJSON \"{fo}\" \"{osm_land_shp}\""
        ops[fo] += f" -clipsrc {lon} {lat} {lon2} {lat2}"
        ops[fo] += " -makevalid"

        # L2 - Land (OSM land polygons)
        fo = os.path.join(dest_l2_path, "land.geojson")
        ops[fo] = f"ogr2ogr -of GeoJSON \"{fo}\" \"{osm_land_shp}\""
        ops[fo] += f" -clipsrc {lon} {lat} {lon2} {lat2}"
        ops[fo] += " -makevalid"

        # L1 - Water (polygons)
        fi = os.path.join(source_l1_path, "water.pbf")
        fo = os.path.join(dest_l1_path, "water.geojson")
        ops[fo] = (f"osmium export \"{fi}\" -f geojson"
                   f" --geometry-types=multipolygon"
                   f" -c \"{water_config}\""
                   f" -o \"{fo}\"")

        # L2 - Water (polygons)
        fi = os.path.join(source_l1_path, "water.pbf")
        fo = os.path.join(dest_l2_path, "water.geojson")
        ops[fo] = (f"osmium export \"{fi}\" -f geojson"
                   f" --geometry-types=multipolygon"
                   f" -c \"{water_config}\""
                   f" -o \"{fo}\"")

        # L1 - Urban
        fi = os.path.join(source_l1_path, "urban.pbf")
        fo = os.path.join(dest_l1_path, "urban.geojson")
        ops[fo] = (f"osmium export \"{fi}\" -f geojson"
                   f" --geometry-types=polygon,multipolygon"
                   f" -c \"{urban_config}\""
                   f" -o \"{fo}\"")

        # L2 - Urban
        fi = os.path.join(source_l2_path, "urban.pbf")
        fo = os.path.join(dest_l2_path, "urban.geojson")
        ops[fo] = (f"osmium export \"{fi}\" -f geojson"
                   f" --geometry-types=polygon,multipolygon"
                   f" -c \"{urban_config}\""
                   f" -o \"{fo}\"")

        # L2 - Forest
        fi = os.path.join(source_l2_path, "forest.pbf")
        fo = os.path.join(dest_l2_path, "forest.geojson")
        ops[fo] = (f"osmium export \"{fi}\" -f geojson"
                   f" --geometry-types=polygon,multipolygon"
                   f" -c \"{forest_config}\""
                   f" -o \"{fo}\"")

        # Grass disabled — see bottom of file

        # L1 - Forest (same forest_config as L2)
        l1_forest = os.path.join(dest_l1_path, "forest.geojson")
        fi = os.path.join(source_l1_path, "forest.pbf")
        ops[l1_forest] = (f"osmium export \"{fi}\" -f geojson"
                          f" --geometry-types=polygon,multipolygon"
                          f" -c \"{forest_config}\""
                          f" -o \"{l1_forest}\"")

        # L1 - Roads
        fi = os.path.join(source_l1_path, "roads.pbf")
        fo = os.path.join(dest_l1_path, "roads.geojson")
        ops[fo] = f"osmium export \"{fi}\" -f geojson -c \"{roads_config}\" -o \"{fo}\""

        # L1 - Railways
        fi = os.path.join(source_l1_path, "railways.pbf")
        fo = os.path.join(dest_l1_path, "railways.geojson")
        ops[fo] = f"osmium export \"{fi}\" -f geojson -c \"{railways_config}\" -o \"{fo}\""

        # L1 - Ferries
        fi = os.path.join(source_l1_path, "ferries.pbf")
        fo = os.path.join(dest_l1_path, "ferries.geojson")
        ops[fo] = f"osmium export \"{fi}\" -f geojson -c \"{ferries_config}\" -o \"{fo}\""

        # L1 - Waterways (linestrings only — no polygons)
        fi = os.path.join(source_l1_path, "waterways.pbf")
        fo = os.path.join(dest_l1_path, "waterways.geojson")
        ops[fo] = f"osmium export \"{fi}\" -f geojson --geometry-types=linestring -c \"{waterways_config}\" -o \"{fo}\""

        # Strip properties from L1 polygon files
        # This removes all OSM tags to avoid SQL statement length limits during simplification
        # Use -select with empty string to keep only geometry (works with unnamed geometry fields)
        polygon_files = ["forest"]
        for pf in polygon_files:
            fo = os.path.join(dest_l1_path, f"{pf}.geojson")
            if fo in ops:  # Only if export was scheduled
                temp_file = fo + ".temp"
                strip_cmd = f"rm -f \"{temp_file}\" && ogr2ogr -f GeoJSON \"{temp_file}\" \"{fo}\" -select \"\""
                strip_cmd += f" && mv \"{temp_file}\" \"{fo}\""
                ops[fo + "_strip"] = strip_cmd

        # L2 - Roads
        fi = os.path.join(source_l2_path, "roads.pbf")
        fo = os.path.join(dest_l2_path, "roads.geojson")
        ops[fo] = f"osmium export \"{fi}\" -f geojson -c \"{roads_config}\" -o \"{fo}\""

        # L2 - Railways
        fi = os.path.join(source_l2_path, "railways.pbf")
        fo = os.path.join(dest_l2_path, "railways.geojson")
        ops[fo] = f"osmium export \"{fi}\" -f geojson -c \"{railways_config}\" -o \"{fo}\""

        # L2 - Ferries
        fi = os.path.join(source_l2_path, "ferries.pbf")
        fo = os.path.join(dest_l2_path, "ferries.geojson")
        ops[fo] = f"osmium export \"{fi}\" -f geojson -c \"{ferries_config}\" -o \"{fo}\""

        # L2 - Waterways (linestrings only — no polygons)
        fi = os.path.join(source_l2_path, "waterways.pbf")
        fo = os.path.join(dest_l2_path, "waterways.geojson")
        ops[fo] = f"osmium export \"{fi}\" -f geojson --geometry-types=linestring -c \"{waterways_config}\" -o \"{fo}\""

        # L1 - Borders (country borders as linestrings)
        fi = os.path.join(source_l1_path, "borders.pbf")
        fo = os.path.join(dest_l1_path, "borders.geojson")
        ops[fo] = (f'osmium export "{fi}" -f geojson'
                   f' --geometry-types=linestring'
                   f' -c "{borders_config}"'
                   f' -o "{fo}"')

        # L2 - Borders (country borders as linestrings)
        fi = os.path.join(source_l2_path, "borders.pbf")
        fo = os.path.join(dest_l2_path, "borders.geojson")
        ops[fo] = (f'osmium export "{fi}" -f geojson'
                   f' --geometry-types=linestring'
                   f' -c "{borders_config}"'
                   f' -o "{fo}"')

        # L1 - Places (point nodes only)
        fi = os.path.join(source_l1_path, "places.pbf")
        fo = os.path.join(dest_l1_path, "places.geojson")
        ops[fo] = (f"osmium export \"{fi}\" -f geojson"
                   f" --geometry-types=point"
                   f" -c \"{places_config}\""
                   f" -o \"{fo}\"")

        # L2 - Places (point nodes only)
        fi = os.path.join(source_l2_path, "places.pbf")
        fo = os.path.join(dest_l2_path, "places.geojson")
        ops[fo] = (f"osmium export \"{fi}\" -f geojson"
                   f" --geometry-types=point"
                   f" -c \"{places_config}\""
                   f" -o \"{fo}\"")

        # Sequential file processing - process one file at a time, stop on first error
        print(f"  Processing files sequentially")

        # STEP 1: Export files sequentially
        export_tasks = [(fo, cmd) for fo, cmd in ops.items()]
        print(f"\n  STEP 1: Exporting {len(export_tasks)} files")
        _t = time.perf_counter()
        for i, task in enumerate(export_tasks):
            file_path, cmd = task
            print(f"    [{i+1}/{len(export_tasks)}] Exporting {os.path.basename(file_path)}...")
            print(f"    Command: {cmd}")
            result = process_file_export(task)
            if not result[1]:  # result is (file_path, success, error_message)
                error_msg = f"Export failed for {file_path}\n"
                error_msg += f"    Command: {cmd}\n"
                error_msg += f"    Error: {result[2]}"
                return (tile_id, False, error_msg, _step_times)
        _step_times["step_1"] = time.perf_counter() - _t
        print(f"    ✓ Done ({_step_times['step_1']:.1f}s)")

        # STEP 2: Clean null/empty properties from roads files
        # This ensures bridge/tunnel filters work correctly (has bridge vs null bridge)
        print(f"\n  STEP 2: Cleaning null properties from roads files")
        _t = time.perf_counter()
        l1_roads = os.path.join(dest_l1_path, "roads.geojson")
        l2_roads = os.path.join(dest_l2_path, "roads.geojson")
        if os.path.exists(l1_roads):
            print(f"    Cleaning L1 roads...")
            clean_geojson_properties(l1_roads)
        if os.path.exists(l2_roads):
            print(f"    Cleaning L2 roads...")
            clean_geojson_properties(l2_roads)
        _step_times["step_2"] = time.perf_counter() - _t
        print(f"    ✓ Done ({_step_times['step_2']:.1f}s)")

        # STEP 2.5: Extract highway labels (A/E numbers from motorways/trunks)
        print(f"\n  STEP 2.5: Extracting highway labels")
        _t = time.perf_counter()
        highway_label_pairs = [
            (os.path.join(dest_l1_path, "roads.geojson"), os.path.join(dest_l1_path, "highway_labels.geojson")),
            (os.path.join(dest_l2_path, "roads.geojson"), os.path.join(dest_l2_path, "highway_labels.geojson")),
        ]
        for hl_source, hl_dest in highway_label_pairs:
            if not os.path.exists(hl_source):
                print(f"    - Skipping {hl_source} (not found)")
                continue
            print(f"    - Extracting {os.path.basename(hl_dest)} from {os.path.dirname(hl_source)}...")
            hl_result = process_file_highway_labels(hl_source, hl_dest)
            if not hl_result[1]:
                return (tile_id, False, f"Highway labels extraction failed for {hl_dest}\n    Error: {hl_result[2]}", _step_times)
        _step_times["step_2_5"] = time.perf_counter() - _t
        print(f"    ✓ Done ({_step_times['step_2_5']:.1f}s)")

        # STEP 4: Add minzoom to places and convert to tippecanoe format
        places_level_files = [
            (os.path.join(dest_l1_path, "places.geojson"), "L1"),
            (os.path.join(dest_l2_path, "places.geojson"), "L2"),
        ]
        print(f"\n  STEP 4: Adding minzoom to places")
        _t = time.perf_counter()
        for places_path, level in places_level_files:
            if not os.path.exists(places_path):
                print(f"    - Skipping {places_path} (not found)")
                continue
            ok = add_places_minzoom(places_path, level)
            if not ok:
                return (tile_id, False, f"places minzoom failed for {places_path}", _step_times)
            transform_minzoom_to_tippecanoe(places_path)
            print(f"    ✓ {places_path}")
        _step_times["step_4"] = time.perf_counter() - _t
        print(f"    ✓ Done ({_step_times['step_4']:.1f}s)")

        # STEP 5: Filter water polygons by area
        water_filter_tasks = _build_water_filter_tasks(ops)
        print(f"\n  STEP 5: Filtering water polygons by area ({len(water_filter_tasks)} files)")
        _t = time.perf_counter()
        for i, task in enumerate(water_filter_tasks):
            file_path = task[0]
            print(f"    [{i+1}/{len(water_filter_tasks)}] Filtering {os.path.basename(file_path)} (threshold {task[1]} deg²)...")
            result = process_file_water_filter(task)
            if not result[1]:
                error_msg = f"Water area filter failed for {file_path}\n"
                error_msg += f"    Error: {result[2]}"
                return (tile_id, False, error_msg, _step_times)
        _step_times["step_5"] = time.perf_counter() - _t
        print(f"    ✓ Done ({_step_times['step_5']:.1f}s)")

        # STEP 6: Filter L1 land polygons by area
        land_filter_tasks = _build_land_filter_tasks(ops)
        print(f"\n  STEP 6: Filtering land polygons by area ({len(land_filter_tasks)} files)")
        _t = time.perf_counter()
        for i, task in enumerate(land_filter_tasks):
            file_path = task[0]
            print(f"    [{i+1}/{len(land_filter_tasks)}] Filtering {os.path.basename(file_path)} (threshold {task[1]} deg²)...")
            result = process_file_land_filter(task)
            if not result[1]:
                error_msg = f"Land area filter failed for {file_path}\n"
                error_msg += f"    Error: {result[2]}"
                return (tile_id, False, error_msg, _step_times)
        _step_times["step_6"] = time.perf_counter() - _t
        print(f"    ✓ Done ({_step_times['step_6']:.1f}s)")

        # STEP 7: Filter forest polygons by area and compactness
        forest_filter_tasks = _build_forest_filter_tasks(ops)
        print(f"\n  STEP 7: Filtering forest polygons by area+compactness ({len(forest_filter_tasks)} files)")
        _t = time.perf_counter()
        for i, task in enumerate(forest_filter_tasks):
            fo, area_threshold, compactness_threshold = task
            print(f"    [{i+1}/{len(forest_filter_tasks)}] Filtering {os.path.basename(fo)} "
                  f"(area>={area_threshold}, PP>={compactness_threshold})...")
            if not filter_forest_by_shape(fo, area_threshold, compactness_threshold):
                return (tile_id, False, f"Forest shape filter failed for {fo}", _step_times)
        _step_times["step_7"] = time.perf_counter() - _t
        if forest_filter_tasks:
            print(f"    ✓ Done ({_step_times['step_7']:.1f}s)")

        # STEP 8: Simplify files sequentially
        simplify_tasks = _build_simplify_tasks(ops)
        print(f"\n  STEP 8: Simplifying {len(simplify_tasks)} files")
        _t = time.perf_counter()
        for i, task in enumerate(simplify_tasks):
            file_path = task[0]
            print(f"    [{i+1}/{len(simplify_tasks)}] Simplifying {os.path.basename(file_path)}...")
            result = process_file_simplify(task)
            if not result[1]:  # result is (file_path, success, error_message)
                error_msg = f"Simplification failed for {file_path}\n"
                error_msg += f"    Error: {result[2]}"
                return (tile_id, False, error_msg, _step_times)
        _step_times["step_8"] = time.perf_counter() - _t
        print(f"    ✓ Done ({_step_times['step_8']:.1f}s)")

        # STEP 9: Stamp tippecanoe minzoom hints on water features
        # NOTE: Must run after simplification (STEP 8) — ogr2ogr may corrupt nested
        # JSON objects if stamped earlier, causing tippecanoe to ignore the hint.
        print(f"\n  STEP 9: Adding tippecanoe minzoom hints to water")
        _t = time.perf_counter()
        for task in water_filter_tasks:
            file_path = task[0]
            level = 'L1' if '/L1/' in file_path else 'L2'
            minzoom = WATER_MINZOOM[level]
            print(f"    - {os.path.basename(file_path)} (minzoom={minzoom})")
            if not add_water_minzoom(file_path, minzoom):
                return (tile_id, False, f"Water minzoom failed for {file_path}", _step_times)
        _step_times["step_9"] = time.perf_counter() - _t
        print(f"    ✓ Done ({_step_times['step_9']:.1f}s)")

        # STEP 10: Stamp tippecanoe minzoom hints on forest features
        # NOTE: Must run after simplification (STEP 8) — same reason as STEP 9.
        print(f"\n  STEP 10: Adding tippecanoe minzoom hints to forest")
        _t = time.perf_counter()
        for fo, _, _ in forest_filter_tasks:
            level = 'L2' if '/L2/' in fo else 'L1'
            if level not in FOREST_MINZOOM:
                continue
            minzoom = FOREST_MINZOOM[level]
            print(f"    - {os.path.basename(fo)} (minzoom={minzoom})")
            if not add_water_minzoom(fo, minzoom):
                return (tile_id, False, f"Forest minzoom failed for {fo}", _step_times)
        _step_times["step_10"] = time.perf_counter() - _t
        if forest_filter_tasks:
            print(f"    ✓ Done ({_step_times['step_10']:.1f}s)")

        # z_order processing is disabled (deprecated light/dark/tileviewer styles only)
        # waterway width filtering is disabled — see bottom of file

        # STEP 11: Tag motorway_link connectivity type (L1 and L2 roads)
        def make_neighbor_tile_id(nlat: int, nlon: int) -> str:
            nlat_id = f"S{-nlat:02d}" if nlat < 0 else f"N{nlat:02d}"
            nlon_id = f"W{-nlon:03d}" if nlon < 0 else f"E{nlon:03d}"
            return f"{nlat_id}_{nlon_id}"

        def get_neighbor_roads(level: str) -> list:
            paths = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    nlat = lat + dy * tile_size
                    nlon = lon + dx * tile_size
                    ntid = make_neighbor_tile_id(nlat, nlon)
                    npath = os.path.join(source_path, ntid, level, "geojson", "roads.geojson")
                    if os.path.exists(npath):
                        paths.append(npath)
            return paths

        roads_level_files = [
            (os.path.join(dest_l1_path, "roads.geojson"), "L1"),
            (os.path.join(dest_l2_path, "roads.geojson"), "L2"),
        ]
        print(f"\n  STEP 11: Tagging motorway_link connectivity ({len(roads_level_files)} files)")
        _t = time.perf_counter()
        for roads_path, level in roads_level_files:
            if not os.path.exists(roads_path):
                print(f"    - Skipping {roads_path} (not found)")
                continue
            neighbor_paths = get_neighbor_roads(level)
            print(f"    - Tagging {os.path.basename(roads_path)} in {os.path.dirname(roads_path)}...")
            ok = tag_motorway_link_connectivity(roads_path, neighbor_paths=neighbor_paths)
            if not ok:
                return (tile_id, False, f"motorway_link tagging failed for {roads_path}", _step_times)
        _step_times["step_11"] = time.perf_counter() - _t
        print(f"    ✓ Done ({_step_times['step_11']:.1f}s)")

        return (tile_id, True, None, _step_times)

    except Exception as e:
        return (tile_id, False, str(e), _step_times)


def process_file_export(args):
    """Export a single OSM file to GeoJSON (Step 1).

    Args:
        args: Tuple of (output_path, command)

    Returns:
        Tuple of (output_path, success, error_message)
    """
    import subprocess

    fo, cmd = args
    if os.path.exists(fo):
        return (fo, True, "already exists")

    try:
        #result = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        return (fo, True, None)
    except subprocess.CalledProcessError as ex:
        # Include both stdout and stderr for debugging
        error_msg = f"Command failed with exit code {ex.returncode}"
        if ex.stderr:
            error_msg += f"\nStderr: {ex.stderr}"
        if ex.stdout:
            error_msg += f"\nStdout: {ex.stdout}"
        return (fo, False, error_msg)


def process_file_simplify(args):
    """Simplify a GeoJSON file (Step 2).

    Args:
        args: Tuple of (file_path, tolerance_level, file_type)
        - tolerance_level: 'L1' or 'L2'
        - file_type: 'roads' or 'polygons'

    Returns:
        Tuple of (file_path, success, error_message)
    """
    fo, level, file_type = args

    if level not in SIMPLIFICATION_TOLERANCES:
        return (fo, True, "no simplification needed")

    # Check if this file type should be simplified at this level
    if file_type not in SIMPLIFICATION_TOLERANCES[level]:
        print(f"    - Skipping simplification for {fo} (preserving original geometry)")
        return (fo, True, "no simplification needed")

    tolerance = SIMPLIFICATION_TOLERANCES[level][file_type]
    print(f"    - Simplifying {fo} (tolerance {tolerance}°)")

    # Use wildcard - osmium configs already limit exported properties
    success = simplify_geojson(fo, tolerance)
    return (fo, success, None if success else "simplification failed")


def process_file_zorder(args):
    """Add z_order and minzoom to a GeoJSON file (Step 3).

    Args:
        args: Tuple of (file_path, zorder_type) or (file_path, zorder_type, level) for roads
        - zorder_type: 'roads', 'waterways', 'urban', 'forest', or 'background'
        - level: 'L0', 'L1', or 'L2' (only for roads)

    Returns:
        Tuple of (file_path, success, error_message)
    """
    # Handle both old format (2-tuple) and new format (3-tuple for roads)
    if len(args) == 3:
        fo, zorder_type, level = args
    else:
        fo, zorder_type = args
        level = None

    print(f"    - Adding z_order to {fo} (type: {zorder_type})")

    if zorder_type == 'roads':
        success = add_road_zorder(fo)
        if success and level:
            # Determine if this is Natural Earth or OSM roads
            source = 'natural_earth' if '/L0/' in fo else 'osm'
            print(f"    - Adding minzoom to {fo} (level: {level}, source: {source})")
            success = add_road_minzoom(fo, level, source)
    elif zorder_type == 'railways':
        success = add_railway_zorder(fo)
        if success and level:
            print(f"    - Adding minzoom to {fo} (level: {level})")
            success = add_railway_minzoom(fo, level)
    elif zorder_type == 'waterways':
        success = add_background_zorder(fo, 150)
    elif zorder_type == 'urban':
        success = add_background_zorder(fo, 20)
    elif zorder_type == 'forest':
        success = add_background_zorder(fo, 15)
    else:  # background
        success = add_background_zorder(fo, 10)

    return (fo, success, None if success else "z_order/minzoom failed")


def filter_forest_by_shape(geojson_path: str, min_area: float, min_compactness: float) -> bool:
    """Filter forest polygons by area and Polsby-Popper compactness in a single ogr2ogr pass.

    Polsby-Popper score = 4π × area / perimeter²  (0–1, 1 = circle).
    Removes sub-pixel fragments (area filter) and narrow strips (compactness filter).

    Uses ST_Length(ST_Boundary(geometry)) for perimeter — portable across both
    Polygon and MultiPolygon geometries in SpatiaLite on Debian/Ubuntu.

    Writes atomically via temp file + os.replace.
    """
    import subprocess
    import os

    layer_name, _, is_empty = get_geojson_metadata(geojson_path)

    if is_empty:
        return True

    try:
        temp_output = f"{geojson_path}.tmp"

        if os.path.exists(temp_output):
            os.remove(temp_output)

        cmd = f"ogr2ogr -f GeoJSON \"{temp_output}\" \"{geojson_path}\""
        cmd += " -dialect SQLITE"
        cmd += (
            f" -sql \"SELECT geometry, * FROM \\\"{layer_name}\\\" WHERE"
            f" ST_Area(geometry) > {min_area}"
            f" AND (4.0 * 3.14159265 * ST_Area(geometry)) /"
            f" (ST_Length(ST_Boundary(geometry)) * ST_Length(ST_Boundary(geometry)))"
            f" > {min_compactness}\""
        )

        subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        os.replace(temp_output, geojson_path)
        return True

    except subprocess.CalledProcessError as e:
        print(f"Error filtering forest by shape in {geojson_path}: {e.stderr if e.stderr else str(e)}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False
    except Exception as e:
        print(f"Error filtering forest by shape in {geojson_path}: {e}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False


def process_file_water_filter(args):
    """Filter a water-polygon GeoJSON by minimum area (Step 2.6).

    Args:
        args: Tuple of (file_path, min_area_deg2)

    Returns:
        Tuple of (file_path, success, error_message)
    """
    fo, min_area_deg2 = args
    print(f"    - Filtering water polygons {fo} (min area {min_area_deg2} deg²)")
    success = filter_water_by_area(fo, min_area_deg2)
    return (fo, success, None if success else "water area filter failed")


def process_file_land_filter(args):
    """Filter a land-polygon GeoJSON by minimum area (Step 2.7).

    Uses filter_land_by_area which does simple area filtering without name grouping.

    Args:
        args: Tuple of (file_path, min_area_deg2)

    Returns:
        Tuple of (file_path, success, error_message)
    """
    fo, min_area_deg2 = args
    print(f"    - Filtering land polygons {fo} (min area {min_area_deg2} deg²)")
    success = filter_land_by_area(fo, min_area_deg2)
    return (fo, success, None if success else "land area filter failed")


def process_file_highway_labels(source_path: str, dest_path: str) -> tuple:
    """Extract A/E highway label features from a roads GeoJSON (Step 2.5).

    Reads roads.geojson and writes highway_labels.geojson containing one feature
    per A-number or E-number found on motorway/trunk roads.  A road carrying
    ref="A1;E40" produces two separate features with identical geometry so that
    MapLibre line-symbol collision detection can alternate the shields naturally.

    Args:
        source_path: Path to roads.geojson
        dest_path:   Path to highway_labels.geojson (output)

    Returns:
        Tuple of (dest_path, success, error_message)
    """
    import json
    import re

    # Matches European motorway/trunk ref formats:
    #   A/E/M/D  + optional space/hyphen + digits  (most of Europe)
    #   AP       + optional space/hyphen + digits  (Spanish toll motorways)
    #   S        + digits                          (Polish expressways, e.g. S3, S8)
    #   R        + digits                          (Hungarian expressways, e.g. R61, R67)
    #   DE       + digits                          (Romanian expressways, e.g. DE1)
    # Internal spaces (e.g. German "A 3") and hyphens (Spanish "A-1") are both allowed.
    REF_PATTERN = re.compile(r'^(AP|DE|[AEMDRSaemdrs])\s*[-]?\s*\d+$')

    try:
        with open(source_path) as f:
            data = json.load(f)

        output_features = []
        for feat in data.get('features', []):
            props = feat.get('properties') or {}
            if props.get('highway') not in ('motorway', 'trunk'):
                continue
            ref = props.get('ref') or ''
            if not ref:
                continue
            for piece in ref.split(';'):
                piece = piece.strip()
                if REF_PATTERN.match(piece):
                    # Strip internal spaces ("A 3" → "A3") but keep hyphens ("A-1" → "A-1")
                    piece_normalized = re.sub(r'\s+', '', piece.upper())
                    # E-routes → green; everything else (A, AP, M, D) → blue
                    ref_type = 'E' if piece_normalized.startswith('E') else 'A'
                    output_features.append({
                        'type': 'Feature',
                        'geometry': feat['geometry'],
                        'properties': {
                            'ref': piece_normalized,
                            'ref_type': ref_type,
                        },
                    })

        # Minzoom per highway class for national road shields
        NATIONAL_MINZOOM = {
            'trunk':     10,   # e.g. French N7, Norwegian Rv tagged trunk
            'primary':   10,
            'secondary': 11,
            'tertiary':  12,
        }
        # D excluded: bare D+num is already handled by REF_PATTERN above
        NATIONAL_ROAD_PATTERN = re.compile(
            r'^(SS|SP|DN|DJ|DK|DW|Rv|Fv|[NB])\s*[-]?\s*\d+$',
            re.IGNORECASE
        )

        for feat in data.get('features', []):
            props = feat.get('properties') or {}
            hw = props.get('highway')
            if hw not in NATIONAL_MINZOOM:
                continue
            ref = props.get('ref') or ''
            if not ref:
                continue
            for piece in ref.split(';'):
                piece = piece.strip()
                if NATIONAL_ROAD_PATTERN.match(piece):
                    piece_normalized = re.sub(r'\s+', '', piece.upper())
                    output_features.append({
                        'type': 'Feature',
                        'geometry': feat['geometry'],
                        'properties': {
                            'ref': piece_normalized,
                            'ref_type': 'N',
                        },
                        'tippecanoe': {'minzoom': NATIONAL_MINZOOM[hw]},
                    })

        with open(dest_path, 'w') as f:
            json.dump({'type': 'FeatureCollection', 'features': output_features}, f)

        return (dest_path, True, None)

    except Exception as e:
        return (dest_path, False, str(e))


def filter_water_by_area(geojson_path: str, min_area_deg2: float) -> bool:
    """Remove water polygons whose connected component is both area-small and spatially compact.

    Tag-based exemptions (always kept regardless of size):
    - waterway tag set (riverbank, canal, dock polygon)
    - water tag in WATER_KEEP_TYPES (river, canal, stream, oxbow, tidal_channel, moat)

    For remaining features, a connected-component (union-find on touching/intersecting
    polygons via shapely STRtree) is kept if EITHER:
    - total component polygon area >= min_area_deg2, OR
    - bounding-box longest dimension >= WATER_BBOX_MIN_DIM (~33 km)

    The bbox condition preserves narrow rivers spanning a tile even when their
    polygon area is below threshold. Isolated ponds satisfy neither condition.

    Writes atomically via temp file + os.replace.
    """
    import json
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    from collections import defaultdict

    try:
        with open(geojson_path) as f:
            data = json.load(f)

        features = data.get('features', [])
        if not features:
            return True

        # Split: always-keep (tagged flowing) vs candidates for spatial filtering
        always_keep_idx = set()
        candidate_idx = []
        for i, feat in enumerate(features):
            props = feat.get('properties') or {}
            waterway = props.get('waterway')
            water = props.get('water')
            if waterway is not None or water in WATER_KEEP_TYPES:
                always_keep_idx.add(i)
            else:
                candidate_idx.append(i)

        if not candidate_idx:
            return True  # all features are tagged flowing water

        # Build shapely geometries for candidates
        cand_geoms = []
        for i in candidate_idx:
            try:
                g = shape(features[i]['geometry'])
            except Exception:
                g = None
            cand_geoms.append(g)

        valid_local = [j for j, g in enumerate(cand_geoms) if g is not None and not g.is_empty]
        valid_geoms = [cand_geoms[j] for j in valid_local]

        # Candidates with bad geometry are kept defensively
        bad_local = set(range(len(cand_geoms))) - set(valid_local)
        keep_candidate_local = set(bad_local)

        if valid_geoms:
            tree = STRtree(valid_geoms)

            parent = list(range(len(valid_geoms)))

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(x, y):
                px, py = find(x), find(y)
                if px != py:
                    parent[px] = py

            for li, geom in enumerate(valid_geoms):
                for lj in tree.query(geom, predicate='touches'):
                    if lj > li:
                        union(li, lj)
                for lj in tree.query(geom, predicate='intersects'):
                    if lj > li:
                        union(li, lj)

            comp_area = defaultdict(float)
            comp_bbox = {}
            for li, geom in enumerate(valid_geoms):
                root = find(li)
                comp_area[root] += geom.area
                b = geom.bounds
                if root not in comp_bbox:
                    comp_bbox[root] = list(b)
                else:
                    cb = comp_bbox[root]
                    cb[0] = min(cb[0], b[0])
                    cb[1] = min(cb[1], b[1])
                    cb[2] = max(cb[2], b[2])
                    cb[3] = max(cb[3], b[3])

            def bbox_max_dim(root):
                cb = comp_bbox.get(root)
                return max(cb[2] - cb[0], cb[3] - cb[1]) if cb else 0.0

            for li in valid_local:
                root = find(li)
                if comp_area[root] >= min_area_deg2 or bbox_max_dim(root) >= WATER_BBOX_MIN_DIM:
                    keep_candidate_local.add(li)

        keep_orig = set(always_keep_idx)
        for li in keep_candidate_local:
            keep_orig.add(candidate_idx[li])

        data['features'] = [f for i, f in enumerate(features) if i in keep_orig]

        temp_output = f"{geojson_path}.tmp"
        with open(temp_output, 'w') as f:
            json.dump(data, f)
        os.replace(temp_output, geojson_path)
        return True

    except Exception as e:
        print(f"Error filtering water by area in {geojson_path}: {e}")
        return False


def add_water_minzoom(geojson_path: str, minzoom: int) -> bool:
    """Stamp every water feature with a tippecanoe minzoom hint.

    Without this, tippecanoe's --drop-densest-as-needed algorithm makes
    per-tile decisions that cause non-monotonic pop-in/pop-out at tile
    boundaries (e.g. appears at Z8, absent at Z9).

    Writes atomically via temp file + os.replace.
    """
    import json

    try:
        with open(geojson_path) as f:
            data = json.load(f)

        for feat in data.get('features', []):
            feat.setdefault('tippecanoe', {})['minzoom'] = minzoom

        temp_output = f"{geojson_path}.tmp"
        with open(temp_output, 'w') as f:
            json.dump(data, f)
        os.replace(temp_output, geojson_path)
        return True

    except Exception as e:
        print(f"Error adding water minzoom in {geojson_path}: {e}")
        return False


def filter_land_by_area(geojson_path: str, min_area_deg2: float) -> bool:
    """Remove land polygons smaller than min_area_deg2 using ogr2ogr.

    Land polygons do NOT have a 'name' column. This function simply filters
    by individual polygon area with no grouping logic.

    Uses ST_Area() on the geometry in SQLITE dialect. Writes to a temp file
    then does an atomic os.replace to avoid partial files on interruption.

    Args:
        geojson_path: Path to the land-polygon GeoJSON file (modified in place)
        min_area_deg2: Minimum polygon area in square degrees

    Returns:
        True if successful, False otherwise
    """
    import subprocess
    import os

    layer_name, _, is_empty = get_geojson_metadata(geojson_path)

    if is_empty:
        return True

    try:
        temp_output = f"{geojson_path}.tmp"

        if os.path.exists(temp_output):
            os.remove(temp_output)

        cmd = f"ogr2ogr -f GeoJSON \"{temp_output}\" \"{geojson_path}\""
        cmd += " -dialect SQLITE"
        # Land polygons: Simple area filtering (no name column)
        cmd += (f" -sql \"SELECT geometry, * FROM \\\"{layer_name}\\\" WHERE"
                f" ST_Area(geometry) > {min_area_deg2}\"")

        subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        os.replace(temp_output, geojson_path)

        return True

    except subprocess.CalledProcessError as e:
        print(f"Error filtering land by area in {geojson_path}: {e.stderr if e.stderr else str(e)}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False
    except Exception as e:
        print(f"Error filtering land by area in {geojson_path}: {e}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False


def get_geojson_metadata(geojson_path: str) -> tuple[str, int, bool]:
    """Get GeoJSON metadata in a single ogrinfo call.

    Combines functionality of is_geojson_empty() and get_geojson_layer_name()
    to avoid redundant subprocess calls.

    Args:
        geojson_path: Path to the GeoJSON file

    Returns:
        Tuple of (layer_name, feature_count, is_empty)
        - layer_name: Name of the first layer (e.g., "OGRGeoJSON")
        - feature_count: Number of features in the layer
        - is_empty: True if feature_count == 0

    Raises:
        RuntimeError: If ogrinfo fails or cannot parse metadata
    """
    import os
    import subprocess
    import json

    # Quick check: empty files are < 100 bytes
    file_size = os.path.getsize(geojson_path)
    if file_size < 100:
        # For truly empty files, return placeholder layer name
        # (will never be used since is_empty=True)
        return ("OGRGeoJSON", 0, True)

    # Single ogrinfo call to get both layer name and feature count
    try:
        result = subprocess.run(
            f'ogrinfo -json "{geojson_path}"',
            shell=True,
            check=True,
            capture_output=True,
            text=True
        )

        info = json.loads(result.stdout)

        if 'layers' in info and len(info['layers']) > 0:
            layer_data = info['layers'][0]
            layer_name = layer_data['name']
            feature_count = layer_data.get('featureCount', 0)
            is_empty = (feature_count == 0)

            return (layer_name, feature_count, is_empty)
        else:
            # No layers = empty file
            return ("OGRGeoJSON", 0, True)

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ogrinfo failed: {e.stderr if e.stderr else str(e)}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse ogrinfo output: {e}")


def add_background_zorder(geojson_path: str, z_order: int) -> bool:
    """Add fixed z_order property to background layer features using ogr2ogr streaming.

    Background layers get a fixed z_order to ensure they render below all roads.
    Uses GDAL's streaming architecture - zero in-memory loading.

    Args:
        geojson_path: Path to the GeoJSON file to modify
        z_order: Fixed z_order value to assign to all features

    Returns:
        True if successful, False otherwise
    """
    import subprocess
    import os

    # Single metadata check (replaces 2 ogrinfo calls with 1)
    layer_name, _, is_empty = get_geojson_metadata(geojson_path)

    if is_empty:
        return True

    try:
        # Write to temp file first (atomic operation)
        temp_output = f"{geojson_path}.tmp"

        # Remove temp file if it exists from previous run
        if os.path.exists(temp_output):
            os.remove(temp_output)

        # Use wildcard to preserve all properties exported by osmium configs
        cmd = f"ogr2ogr -f GeoJSON \"{temp_output}\" \"{geojson_path}\""
        cmd += " -dialect SQLITE"
        cmd += f" -sql \"SELECT geometry, *, {z_order} AS z_order FROM \\\"{layer_name}\\\"\""

        subprocess.run(cmd, shell=True, check=True, capture_output=True)

        # Atomic replacement (prevents corruption if interrupted)
        os.replace(temp_output, geojson_path)

        return True

    except subprocess.CalledProcessError as e:
        print(f"Error adding z_order to {geojson_path}: {e.stderr.decode() if e.stderr else str(e)}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False
    except Exception as e:
        print(f"Error adding z_order to {geojson_path}: {e}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False


def simplify_geojson(geojson_path: str, tolerance: float) -> bool:
    """Simplify geometries using ogr2ogr ST_SimplifyPreserveTopology.

    Uses GDAL's streaming architecture - zero in-memory loading.
    Same Douglas-Peucker algorithm as geopandas but through native C/C++.

    Args:
        geojson_path: Path to the GeoJSON file to simplify in-place
        tolerance: Simplification tolerance in degrees (WGS84 coordinates)

    Returns:
        True if successful, False otherwise
    """
    import subprocess
    import os

    # Skip simplification if tolerance is 0
    if tolerance == 0:
        print(f"    - Skipping simplification for {geojson_path} (tolerance=0)")
        return True

    # Single metadata check (replaces 2 ogrinfo calls with 1)
    layer_name, _, is_empty = get_geojson_metadata(geojson_path)

    if is_empty:
        return True

    try:
        # Write to temp file first (atomic operation)
        temp_output = f"{geojson_path}.tmp"

        # Remove temp file if it exists from previous run
        if os.path.exists(temp_output):
            os.remove(temp_output)

        # Use wildcard to preserve all properties exported by osmium configs
        cmd = f"ogr2ogr -f GeoJSON \"{temp_output}\" \"{geojson_path}\""
        cmd += " -dialect SQLITE"
        cmd += " -lco COORDINATE_PRECISION=7"  # Preserve maximum coordinate precision
        cmd += f" -sql \"SELECT ST_SimplifyPreserveTopology(geometry, {tolerance}) AS geometry, * FROM \\\"{layer_name}\\\"\""
        # cmd += " -makevalid"  # REMOVED - Prevents geometry modification

        subprocess.run(cmd, shell=True, check=True, capture_output=True)

        # Atomic replacement (prevents corruption if interrupted)
        os.replace(temp_output, geojson_path)

        return True

    except subprocess.CalledProcessError as e:
        print(f"Error simplifying {geojson_path}: {e.stderr.decode() if e.stderr else str(e)}")
        # Clean up temp file if it exists
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False
    except Exception as e:
        print(f"Error simplifying {geojson_path}: {e}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False


def add_road_zorder(geojson_path: str) -> bool:
    """Add z_order property to road features using ogr2ogr streaming.

    Z-order calculation combines highway type importance with vertical layer positioning:
    - Highway type determines base z_order (motorway=100, trunk=90, ..., path=1)
    - OSM layer tag adds offset for bridges/tunnels:
      * Tunnels (layer < 0): base_z_order (range: 1-100)
      * Normal roads (layer = 0 or missing): base_z_order + 100 (range: 101-200)
      * Bridges (layer > 0): base_z_order + 200 (range: 201-300)

    Uses GDAL's streaming architecture - zero in-memory loading.
    """
    import subprocess
    import os

    # Single metadata check (replaces 2 ogrinfo calls with 1)
    layer_name, _, is_empty = get_geojson_metadata(geojson_path)

    if is_empty:
        return True

    try:
        # Write to temp file first (atomic operation)
        temp_output = f"{geojson_path}.tmp"

        # Remove temp file if it exists from previous run
        if os.path.exists(temp_output):
            os.remove(temp_output)

        # layer_name already retrieved above

        # Build SQL with CASE expressions for z_order calculation
        # Step 1: Calculate base_z_order from highway type
        base_z_order_sql = """
            CASE highway
                WHEN 'motorway' THEN 100
                WHEN 'trunk' THEN 90
                WHEN 'primary' THEN 80
                WHEN 'secondary' THEN 70
                WHEN 'tertiary' THEN 60
                WHEN 'unclassified' THEN 50
                WHEN 'residential' THEN 40
                WHEN 'motorway_link' THEN 30
                WHEN 'trunk_link' THEN 25
                WHEN 'primary_link' THEN 20
                WHEN 'secondary_link' THEN 15
                WHEN 'tertiary_link' THEN 10
                WHEN 'service' THEN 5
                ELSE 10
            END
        """.replace('\n', ' ').strip()

        # Step 2: Apply layer offset based on OSM layer tag
        # COALESCE handles NULL layer values (defaults to 0)
        z_order_sql = f"""
            CASE
                WHEN CAST(COALESCE(layer, 0) AS INTEGER) < 0 THEN ({base_z_order_sql}) + 200
                WHEN CAST(COALESCE(layer, 0) AS INTEGER) > 0 THEN ({base_z_order_sql}) + 360
                ELSE ({base_z_order_sql}) + 280
            END AS z_order
        """.replace('\n', ' ').strip()

        # Use ogr2ogr with computed z_order column
        # Use wildcard to preserve all properties exported by osmium configs
        cmd = f"ogr2ogr -f GeoJSON \"{temp_output}\" \"{geojson_path}\""
        cmd += " -dialect SQLITE"
        cmd += f" -sql \"SELECT geometry, *, {z_order_sql} FROM \\\"{layer_name}\\\"\""

        subprocess.run(cmd, shell=True, check=True, capture_output=True)

        # Atomic replacement (prevents corruption if interrupted)
        os.replace(temp_output, geojson_path)

        return True

    except subprocess.CalledProcessError as e:
        print(f"Error adding z_order to {geojson_path}: {e.stderr.decode() if e.stderr else str(e)}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False
    except Exception as e:
        print(f"Error adding z_order to {geojson_path}: {e}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False


def add_railway_zorder(geojson_path: str) -> bool:
    """Add z_order property to railway features using ogr2ogr streaming.

    Z-order calculation combines railway type with vertical layer positioning:
    - Railway type determines base z_order (rail=3, light_rail=2, subway=1)
    - OSM layer tag adds offset for tunnels/bridges:
      * Tunnels (layer < 0): base + 150 (range: 151-153)
      * Normal (layer = 0): base + 160 (range: 161-163)
      * Bridges (layer > 0): base + 170 (range: 171-173)

    Uses GDAL's streaming architecture - zero in-memory loading.
    """
    import subprocess
    import os

    layer_name, _, is_empty = get_geojson_metadata(geojson_path)

    if is_empty:
        return True

    try:
        temp_output = f"{geojson_path}.tmp"

        if os.path.exists(temp_output):
            os.remove(temp_output)

        # Base z_order from railway type
        base_z_order_sql = """
            CASE railway
                WHEN 'rail' THEN 3
                WHEN 'light_rail' THEN 2
                WHEN 'subway' THEN 1
                ELSE 1
            END
        """.replace('\n', ' ').strip()

        # Apply layer offset
        z_order_sql = f"""
            CASE
                WHEN CAST(COALESCE(layer, 0) AS INTEGER) < 0 THEN ({base_z_order_sql}) + 150
                WHEN CAST(COALESCE(layer, 0) AS INTEGER) > 0 THEN ({base_z_order_sql}) + 170
                ELSE ({base_z_order_sql}) + 160
            END AS z_order
        """.replace('\n', ' ').strip()

        cmd = f"ogr2ogr -f GeoJSON \"{temp_output}\" \"{geojson_path}\""
        cmd += " -dialect SQLITE"
        cmd += f" -sql \"SELECT geometry, *, {z_order_sql} FROM \\\"{layer_name}\\\"\""

        subprocess.run(cmd, shell=True, check=True, capture_output=True)
        os.replace(temp_output, geojson_path)

        return True

    except subprocess.CalledProcessError as e:
        print(f"Error adding z_order to {geojson_path}: {e.stderr.decode() if e.stderr else str(e)}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False
    except Exception as e:
        print(f"Error adding z_order to {geojson_path}: {e}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False


def add_railway_minzoom(geojson_path: str, level: str) -> bool:
    """Add minzoom property to railways based on railway type and level.

    Args:
        geojson_path: Path to GeoJSON file
        level: Level (L1, L2)

    Returns:
        True if successful, False otherwise
    """
    import subprocess
    import os

    layer_name, _, is_empty = get_geojson_metadata(geojson_path)

    if is_empty:
        return True

    # Define minzoom by railway type
    minzoom_map = {
        'L1': {
            'rail': 7,
            'light_rail': 9,
            'subway': 9,
            'default': 9
        },
        'L2': {
            'rail': 7,
            'light_rail': 9,
            'subway': 9,
            'default': 9
        }
    }

    level_map = minzoom_map.get(level, {})

    # Build CASE statement
    case_parts = []
    for railway_type, minzoom in level_map.items():
        if railway_type != 'default':
            case_parts.append(f"WHEN railway = '{railway_type}' THEN {minzoom}")

    default_minzoom = level_map.get('default', 10)
    minzoom_sql = f"CASE {' '.join(case_parts)} ELSE {default_minzoom} END AS minzoom"

    try:
        temp_output = f"{geojson_path}.tmp"

        if os.path.exists(temp_output):
            os.remove(temp_output)

        cmd = f"ogr2ogr -f GeoJSON \"{temp_output}\" \"{geojson_path}\""
        cmd += " -dialect SQLITE"
        cmd += f" -sql \"SELECT geometry, *, {minzoom_sql} FROM \\\"{layer_name}\\\"\""

        subprocess.run(cmd, shell=True, check=True, capture_output=True)
        os.replace(temp_output, geojson_path)

        return True

    except subprocess.CalledProcessError as e:
        print(f"Error adding minzoom to {geojson_path}: {e.stderr.decode() if e.stderr else str(e)}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False
    except Exception as e:
        print(f"Error adding minzoom to {geojson_path}: {e}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False


def add_places_minzoom(geojson_path: str, level: str) -> bool:
    """Add minzoom property to place nodes based on place type and level.

    Args:
        geojson_path: Path to GeoJSON file
        level: Level (L1, L2)

    Returns:
        True if successful, False otherwise
    """
    import subprocess
    import os

    layer_name, _, is_empty = get_geojson_metadata(geojson_path)

    if is_empty:
        return True

    minzoom_map = {
        'L1': {'city': 7, 'town': 8, 'village': 10, 'hamlet': 10, 'default': 10},
        'L2': {'city': 11, 'town': 11, 'village': 11, 'hamlet': 12,
               'suburb': 13, 'neighbourhood': 14, 'default': 13},
    }
    level_map = minzoom_map.get(level, {})
    case_parts = [f"WHEN place = '{k}' THEN {v}"
                  for k, v in level_map.items() if k != 'default']
    default_minzoom = level_map.get('default', 13)
    minzoom_sql = f"CASE {' '.join(case_parts)} ELSE {default_minzoom} END AS minzoom"

    try:
        temp_output = f"{geojson_path}.tmp"

        if os.path.exists(temp_output):
            os.remove(temp_output)

        cmd = f"ogr2ogr -f GeoJSON \"{temp_output}\" \"{geojson_path}\""
        cmd += " -dialect SQLITE"
        cmd += f" -sql \"SELECT geometry, *, {minzoom_sql} FROM \\\"{layer_name}\\\"\""

        subprocess.run(cmd, shell=True, check=True, capture_output=True)
        os.replace(temp_output, geojson_path)

        return True

    except subprocess.CalledProcessError as e:
        print(f"Error adding minzoom to {geojson_path}: {e.stderr.decode() if e.stderr else str(e)}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False
    except Exception as e:
        print(f"Error adding minzoom to {geojson_path}: {e}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False


def add_road_minzoom(geojson_path: str, level: str, source: str = 'osm') -> bool:
    """Add minzoom property to roads based on highway/road type and level.

    Args:
        geojson_path: Path to GeoJSON file
        level: Level (L0, L1, L2)
        source: Data source ('osm' or 'natural_earth')

    Returns:
        True if successful, False otherwise
    """
    import subprocess
    import os

    # Single metadata check
    layer_name, _, is_empty = get_geojson_metadata(geojson_path)

    if is_empty:
        return True

    if source == 'natural_earth':
        # Natural Earth roads use 'type' field, not 'highway'
        # Note: Exact type values need verification from dataset
        minzoom_sql = """
        CASE
            WHEN type = 'Expressway' THEN 3
            WHEN type = 'Major Highway' THEN 5
            ELSE 3
        END AS minzoom
        """
    else:
        # Define minzoom by highway type (for OSM roads)
        minzoom_map = {
            'L0': {
                'motorway': 3,
                'trunk': 5,
                'default': 3
            },
            'L1': {
                'motorway': 3,
                'trunk': 5,
                'primary': 6,
                'secondary': 9,
                'tertiary': 10,
                'motorway_link': 7,
                'trunk_link': 7,
                'primary_link': 7,
                'secondary_link': 9,
                'tertiary_link': 10,
                'default': 6
            },
            'L2': {
                'motorway': 3,
                'trunk': 5,
                'primary': 6,
                'secondary': 7,
                'tertiary': 9,
                'unclassified': 11,
                'residential': 11,
                'service': 13,
                'living_street': 13,
                'motorway_link': 7,
                'trunk_link': 7,
                'primary_link': 7,
                'secondary_link': 9,
                'tertiary_link': 9,
                'default': 10
            }
        }

        level_map = minzoom_map.get(level, {})

        # Build CASE statement for minzoom
        case_parts = []
        for highway_type, minzoom in level_map.items():
            if highway_type != 'default':
                case_parts.append(f"WHEN highway = '{highway_type}' THEN {minzoom}")

        default_minzoom = level_map.get('default', 14)
        minzoom_sql = f"CASE {' '.join(case_parts)} ELSE {default_minzoom} END AS minzoom"

    try:
        # Write to temp file first (atomic operation)
        temp_output = f"{geojson_path}.tmp"

        # Remove temp file if it exists from previous run
        if os.path.exists(temp_output):
            os.remove(temp_output)

        # Add minzoom property using ogr2ogr
        cmd = f"ogr2ogr -f GeoJSON \"{temp_output}\" \"{geojson_path}\""
        cmd += " -dialect SQLITE"
        cmd += f" -sql \"SELECT geometry, *, {minzoom_sql} FROM \\\"{layer_name}\\\"\""

        subprocess.run(cmd, shell=True, check=True, capture_output=True)

        # Atomic replacement (prevents corruption if interrupted)
        os.replace(temp_output, geojson_path)

        return True

    except subprocess.CalledProcessError as e:
        print(f"Error adding minzoom to {geojson_path}: {e.stderr.decode() if e.stderr else str(e)}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False
    except Exception as e:
        print(f"Error adding minzoom to {geojson_path}: {e}")
        if os.path.exists(temp_output):
            os.remove(temp_output)
        return False


def clean_geojson_properties(geojson_path: str) -> bool:
    """Remove null, None, and empty string properties from GeoJSON features.
    
    This ensures optional tags like bridge/tunnel don't appear on features
    where they're not applicable, allowing proper filter logic in styles.
    
    Args:
        geojson_path: Path to GeoJSON file
        
    Returns:
        True on success, False on error
    """
    import json
    
    try:
        with open(geojson_path, 'r') as f:
            data = json.load(f)
        
        features = data.get('features', [])
        cleaned_count = 0
        
        for feat in features:
            props = feat.get('properties', {})
            if not props:
                continue
                
            # Remove null, None, and empty string values
            keys_to_remove = []
            for key, value in props.items():
                if value is None or value == '' or value == []:
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                del props[key]
                cleaned_count += 1
        
        with open(geojson_path, 'w') as f:
            json.dump(data, f, separators=(',', ':'))
        
        print(f"    Cleaned {cleaned_count} null/empty properties from {os.path.basename(geojson_path)}")
        return True
        
    except Exception as e:
        print(f"Error cleaning properties from {geojson_path}: {e}")
        return False


def tag_motorway_link_connectivity(geojson_path: str, neighbor_paths=None) -> bool:
    """Tag motorway_link features with a connectivity type using directed label propagation.

    Uses two global BFS sweeps starting from motorway anchor positions:
    - Sweep A (forward):  labels links reachable forward from motorway → from_motorway
    - Sweep B (backward): labels links reachable backward toward motorway → to_motorway

    Classification:
    - "connector": from_motorway AND to_motorway
    - "off_ramp":  from_motorway only
    - "on_ramp":   to_motorway only
    - "exit":      neither, but a non-link road exists at an endpoint
    - "unknown":   completely isolated (no road found at either endpoint)

    All other features get motorway_link_type="" (empty string, dropped by tippecanoe).

    Args:
        geojson_path: Path to a roads GeoJSON file.
        neighbor_paths: Optional list of neighboring tile road GeoJSON paths for
            boundary resolution. Neighbor features provide topology context but
            are NOT classified themselves.

    Returns:
        True on success, False on error.
    """
    import json
    import math
    from collections import deque

    try:
        with open(geojson_path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading {geojson_path}: {e}")
        return False

    features = data.get('features', [])

    # Check if any motorway_link features exist
    # Only LineString geometries are processed — MultiLineString motorway_links
    # are skipped in the feat_endpoints / grid building loop and would cause
    # a KeyError if included here.
    link_indices = [
        i for i, feat in enumerate(features)
        if feat.get('properties', {}).get('highway') == 'motorway_link'
        and feat.get('geometry', {}).get('type') == 'LineString'
    ]
    if not link_indices:
        return True

    SNAP = 1e-6  # ~0.1 m — safe because osmium preserves exact shared coordinates
    cell_size = SNAP * 2

    # Load neighbor features for topology context (not classified)
    neighbor_features = []
    if neighbor_paths:
        for np_path in neighbor_paths:
            if np_path and os.path.exists(np_path):
                try:
                    with open(np_path, 'r') as f:
                        nd = json.load(f)
                    neighbor_features.extend(nd.get('features', []))
                except Exception:
                    pass

    all_features = features + neighbor_features

    # Build endpoint list and direction-aware feat_endpoints.
    # feat_endpoints[fidx] = (traffic_start, traffic_end) accounting for oneway=-1/reverse.
    # motorway_link is treated as oneway in geometry direction unless oneway tag reverses it.
    endpoints = []  # list of (x, y, hw_type, feat_idx)
    feat_endpoints = {}  # feat_idx → (traffic_start, traffic_end), motorway_link only

    for idx, feat in enumerate(all_features):
        geom = feat.get('geometry', {})
        props = feat.get('properties', {})
        hw = props.get('highway', '')
        if geom.get('type') != 'LineString':
            continue
        coords = geom.get('coordinates', [])
        if len(coords) < 2:
            continue
        if hw == 'motorway_link':
            oneway = props.get('oneway', '')
            if oneway in ('-1', 'reverse'):
                traffic_start = coords[-1]
                traffic_end = coords[0]
            else:
                traffic_start = coords[0]
                traffic_end = coords[-1]
            feat_endpoints[idx] = (traffic_start, traffic_end)
            for coord in (traffic_start, traffic_end):
                endpoints.append((coord[0], coord[1], hw, idx))
        else:
            for coord in (coords[0], coords[-1]):
                endpoints.append((coord[0], coord[1], hw, idx))

    # Build grid index for fast spatial lookup
    grid = {}
    for ep in endpoints:
        cx = math.floor(ep[0] / cell_size)
        cy = math.floor(ep[1] / cell_size)
        key = (cx, cy)
        if key not in grid:
            grid[key] = []
        grid[key].append(ep)

    def links_starting_at(px, py):
        """Yields fidx for motorway_links whose traffic_start ≈ (px, py)."""
        cx = math.floor(px / cell_size)
        cy = math.floor(py / cell_size)
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                for ex, ey, hw, fidx in grid.get((cx + ddx, cy + ddy), []):
                    if hw == 'motorway_link':
                        if abs(ex - px) <= SNAP and abs(ey - py) <= SNAP:
                            s, _ = feat_endpoints[fidx]
                            if abs(s[0] - px) <= SNAP and abs(s[1] - py) <= SNAP:
                                yield fidx

    def links_ending_at(px, py):
        """Yields fidx for motorway_links whose traffic_end ≈ (px, py)."""
        cx = math.floor(px / cell_size)
        cy = math.floor(py / cell_size)
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                for ex, ey, hw, fidx in grid.get((cx + ddx, cy + ddy), []):
                    if hw == 'motorway_link':
                        if abs(ex - px) <= SNAP and abs(ey - py) <= SNAP:
                            _, e = feat_endpoints[fidx]
                            if abs(e[0] - px) <= SNAP and abs(e[1] - py) <= SNAP:
                                yield fidx

    # Road types that do NOT block sweep propagation.
    # motorway: handled as anchor start points, not a ramp boundary.
    # service: maintenance/emergency access lanes appear at interchange nodes
    #          in OSM and should not block sweep through the interchange zone.
    SWEEP_PASSTHROUGH = {'motorway', 'service'}

    def non_link_types_at(px, py):
        """Returns set of non-empty, non-motorway_link highway types at (px, py)."""
        result = set()
        cx = math.floor(px / cell_size)
        cy = math.floor(py / cell_size)
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                for ex, ey, hw, fidx in grid.get((cx + ddx, cy + ddy), []):
                    if abs(ex - px) <= SNAP and abs(ey - py) <= SNAP:
                        if hw and hw != 'motorway_link':
                            result.add(hw)
        return result

    def blocking_types_at(px, py):
        """Returns road types at (px, py) that block sweep propagation.

        Excludes motorway (anchor origin) and service (interchange access lanes)
        so that minor OSM roads at interchange nodes don't break ramp chains.
        """
        result = set()
        cx = math.floor(px / cell_size)
        cy = math.floor(py / cell_size)
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                for ex, ey, hw, fidx in grid.get((cx + ddx, cy + ddy), []):
                    if abs(ex - px) <= SNAP and abs(ey - py) <= SNAP:
                        if hw and hw != 'motorway_link' and hw not in SWEEP_PASSTHROUGH:
                            result.add(hw)
        return result

    def pos_key(px, py):
        return (round(px / SNAP), round(py / SNAP))

    # Build lookup for motorway refs by feature index
    motorway_refs = {}
    for idx, feat in enumerate(all_features):
        props = feat.get('properties', {})
        if props.get('highway') == 'motorway':
            ref = props.get('ref', '')
            motorway_refs[idx] = ref

    # Collect motorway anchor positions: all endpoints of highway=motorway features
    # Track which motorway ref each anchor belongs to for connector detection
    motorway_anchors = []  # (x, y, motorway_ref)
    seen_anchor_keys = set()
    for ex, ey, hw, fidx in endpoints:
        if hw == 'motorway':
            pk = pos_key(ex, ey)
            if pk not in seen_anchor_keys:
                seen_anchor_keys.add(pk)
                ref = motorway_refs.get(fidx, '')
                motorway_anchors.append((ex, ey, ref))

    # Sweep A: forward from motorway anchors → from_motorway dict (→ off_ramp / connector)
    # Maps link_idx -> motorway_ref that reached it
    from_motorway = {}
    visited_a = set()
    queue = deque()
    for ax, ay, src_ref in motorway_anchors:
        pk = pos_key(ax, ay)
        if pk not in visited_a:
            visited_a.add(pk)
            queue.append((ax, ay, src_ref))

    while queue:
        px, py, src_ref = queue.popleft()
        for link_fidx in links_starting_at(px, py):
            if link_fidx in from_motorway:
                continue
            from_motorway[link_fidx] = src_ref
            _, e = feat_endpoints[link_fidx]
            # Stop at local road boundaries; pass through motorway/service nodes
            if blocking_types_at(e[0], e[1]):
                continue
            pk = pos_key(e[0], e[1])
            if pk not in visited_a:
                visited_a.add(pk)
                queue.append((e[0], e[1], src_ref))

    # Sweep B: backward from motorway anchors → to_motorway dict (→ on_ramp / connector)
    # Maps link_idx -> motorway_ref that reached it
    to_motorway = {}
    visited_b = set()
    queue = deque()
    for ax, ay, src_ref in motorway_anchors:
        pk = pos_key(ax, ay)
        if pk not in visited_b:
            visited_b.add(pk)
            queue.append((ax, ay, src_ref))

    while queue:
        px, py, src_ref = queue.popleft()
        for link_fidx in links_ending_at(px, py):
            if link_fidx in to_motorway:
                continue
            to_motorway[link_fidx] = src_ref
            s, _ = feat_endpoints[link_fidx]
            # Stop at local road boundaries; pass through motorway/service nodes
            if blocking_types_at(s[0], s[1]):
                continue
            pk = pos_key(s[0], s[1])
            if pk not in visited_b:
                visited_b.add(pk)
                queue.append((s[0], s[1], src_ref))

    # Classify each motorway_link in this tile (neighbors are context only)
    link_set = set(link_indices)
    for idx in link_indices:
        ref_from = from_motorway.get(idx, '')
        ref_to = to_motorway.get(idx, '')
        is_from = ref_from != ''
        is_to = ref_to != ''

        # Connector: reached from different motorway refs in each direction
        # Both must have refs and they must differ
        if is_from and is_to and ref_from != ref_to:
            link_type = 'connector'
        elif is_from:
            link_type = 'off_ramp'
        elif is_to:
            link_type = 'on_ramp'
        else:
            s, e = feat_endpoints[idx]
            nlt = non_link_types_at(s[0], s[1]) | non_link_types_at(e[0], e[1])
            link_type = 'exit' if nlt else 'unknown'
        features[idx]['properties']['motorway_link_type'] = link_type

    # Set empty string on all non-link features (tippecanoe drops empty strings)
    for idx, feat in enumerate(features):
        if idx not in link_set:
            feat.setdefault('properties', {})['motorway_link_type'] = ''

    try:
        with open(geojson_path, 'w') as f:
            json.dump(data, f, separators=(',', ':'))
    except Exception as e:
        print(f"Error writing {geojson_path}: {e}")
        return False

    return True


def build_mbtiles(
        tmp_tile_dir: str,
        output_tile_dir: str,
        tool: str) -> bool:

    os.makedirs(output_tile_dir, exist_ok=True)

    l1_output_dir = os.path.join(output_tile_dir, "L1")
    l2_output_dir = os.path.join(output_tile_dir, "L2")

    os.makedirs(l1_output_dir, exist_ok=True)
    os.makedirs(l2_output_dir, exist_ok=True)

    l0_file = os.path.join(output_tile_dir, "L0.mbtiles")

    # Generate country label points with minzoom from Natural Earth
    country_labels_file = os.path.join(tmp_tile_dir, "L0", "country_labels.geojson")
    ne_admin0 = os.path.join(tmp_tile_dir, "L0", "ne_10m_admin_0_countries.geojson")

    # The ne_10m_admin_0_countries.geojson was already processed by tiles_pipeline.py:
    # - Field names lowercased (name_en, name_nl, etc.)
    # - minzoom already calculated and stored as a property
    # Generate point features using ST_PointOnSurface from the processed file.
    print("  Generating country label points with minzoom...")
    if os.path.exists(country_labels_file):
        os.remove(country_labels_file)

    try:
        gdf = gpd.read_file(ne_admin0)
        # ST_PointOnSurface equivalent: a point guaranteed to lie within the polygon
        gdf.geometry = gdf.geometry.representative_point()

        # minzoom is already a column from the pre-processing step in tiles_pipeline.py
        name_cols = ['name_en', 'name_nl', 'name_de', 'name_fr', 'name_es', 'name_pt',
                     'name_ru', 'name_ar', 'name_zhs', 'name_zht', 'name_ja', 'name_ko',
                     'name_hi', 'name_it', 'name_pl', 'name_sv', 'name_tr', 'name_el',
                     'name_bn', 'name_fa', 'name_he', 'name_uk', 'name_ur', 'name_vi',
                     'name_hu', 'name_id', 'minzoom']
        keep_cols = ['geometry'] + [c for c in name_cols if c in gdf.columns]
        gdf[keep_cols].to_file(country_labels_file, driver='GeoJSON')
        print("  ✓ Generated country label points")
    except Exception as e:
        raise RuntimeError(f"Failed to generate country label points:\n{e}") from e

    # Transform minzoom to tippecanoe object structure
    transform_minzoom_to_tippecanoe(country_labels_file)
    print("  ✓ Transformed minzoom to tippecanoe format")

    cmd = f"\"{tool}\" -o \"{l0_file}\""
    cmd += " -Z0 -z6"
    cmd += " --drop-densest-as-needed"
    cmd += " --coalesce-densest-as-needed"
    cmd += " --maximum-tile-bytes=2000000"  # 2MB limit (up from 500KB default) to prevent feature drops
    #cmd += " --simplification=3"
    cmd += f" -L \"land:{tmp_tile_dir}/L0/ne_10m_land.geojson\""
    cmd += f" -L \"ocean:{tmp_tile_dir}/L0/ne_10m_ocean.geojson\""
    cmd += f" -L \"urban:{tmp_tile_dir}/L0/ne_10m_urban_areas.geojson\""
    cmd += f" -L \"boundaries:{tmp_tile_dir}/L0/ne_10m_admin_0_countries.geojson\""

    # Preserve all Natural Earth language fields (26 languages) for multilingual country labels
    # Note: Field names are converted to lowercase during GeoJSON processing (see tiles_pipeline.py)
    lang_fields = ["name_en", "name_nl", "name_de", "name_fr", "name_es", "name_pt",
                   "name_ru", "name_ar", "name_zhs", "name_zht", "name_ja", "name_ko",
                   "name_hi", "name_it", "name_pl", "name_sv", "name_tr", "name_el",
                   "name_bn", "name_fa", "name_he", "name_uk", "name_ur", "name_vi",
                   "name_hu", "name_id"]
    cmd += " " + " ".join(f"-y {field}" for field in lang_fields)
    cmd += " -y highway -y minzoom"

    # Add country label points layer (Z0-6 deduplicated country names)
    l0_country_labels = os.path.join(tmp_tile_dir, "L0", "country_labels.geojson")
    if os.path.exists(l0_country_labels):
        cmd += f" -L \"country_labels:{l0_country_labels}\""
    else:
        print(f"  WARNING: {l0_country_labels} not found")
        print(f"  L0.mbtiles will use polygon-based country labels (may duplicate)")

    # Add Natural Earth roads (global coverage for Z4-6)
    l0_roads_geojson = os.path.join(tmp_tile_dir, "L0", "ne_10m_roads.geojson")
    if os.path.exists(l0_roads_geojson):
        transform_minzoom_to_tippecanoe(l0_roads_geojson)
        cmd += f" -L \"roads:{l0_roads_geojson}\""
    else:
        print(f"  WARNING: {l0_roads_geojson} not found")
        print("  L0.mbtiles will NOT include roads layer (Z4-6 coverage)")

    if not os.path.exists(l0_file):
        print(f"    - {l0_file}")
        try:
            subprocess.run(cmd, shell=True, check=True)
        except subprocess.CalledProcessError as ex:
            print(ex)
            return False

    subdirs = [p for p in Path(tmp_tile_dir).iterdir() if p.is_dir()]

    for tile_path in subdirs:
        tile_name = os.path.basename(tile_path)
        if tile_name == "L0":
            continue

        l1_base_dir = os.path.join(tmp_tile_dir, tile_name, "L1", "geojson")
        l2_base_dir = os.path.join(tmp_tile_dir, tile_name, "L2", "geojson")

        l1_land = os.path.join(l1_base_dir, "land.geojson")
        l1_water = os.path.join(l1_base_dir, "water.geojson")
        l1_urban = os.path.join(l1_base_dir, "urban.geojson")
        l1_railways = os.path.join(l1_base_dir, "railways.geojson")
        l1_ferries = os.path.join(l1_base_dir, "ferries.geojson")
        l1_roads = os.path.join(l1_base_dir, "roads.geojson")
        l1_places = os.path.join(l1_base_dir, "places.geojson")
        #l1_waterways = os.path.join(l1_base_dir, "waterways.geojson")
        l1_forest = os.path.join(l1_base_dir, "forest.geojson")
        l1_borders = os.path.join(l1_base_dir, "borders.geojson")
        l1_highway_labels = os.path.join(l1_base_dir, "highway_labels.geojson")
        # l1_grass disabled — see bottom of file

        l2_land = os.path.join(l2_base_dir, "land.geojson")
        l2_water = os.path.join(l2_base_dir, "water.geojson")
        l2_urban = os.path.join(l2_base_dir, "urban.geojson")
        l2_forest = os.path.join(l2_base_dir, "forest.geojson")
        l2_railways = os.path.join(l2_base_dir, "railways.geojson")
        l2_ferries = os.path.join(l2_base_dir, "ferries.geojson")
        l2_roads = os.path.join(l2_base_dir, "roads.geojson")
        l2_waterways = os.path.join(l2_base_dir, "waterways.geojson")
        l2_places = os.path.join(l2_base_dir, "places.geojson")
        l2_borders = os.path.join(l2_base_dir, "borders.geojson")
        l2_highway_labels = os.path.join(l2_base_dir, "highway_labels.geojson")

        ops = {}

        # L1 mbtiles — land + water + roads
        fo = os.path.join(l1_output_dir, f"{tile_name}.mbtiles")
        ops[fo] = f"\"{tool}\" -o \"{fo}\""
        ops[fo] += " -Z7 -z10"
        # ops[fo] += " --drop-rate=0.98"  # REMOVED - Test Option C
        #ops[fo] += " --simplification=7"  # Higher simplification at low zoom
        #ops[fo] += " --simplification=3"  # Higher simplification at low zoom
        ops[fo] += " --buffer=64"  # Prevent edge artifacts
        ops[fo] += " --maximum-tile-bytes=20000000"  # 20MB limit (down from 40MB - removed 3 dense layers)
        ops[fo] += " --maximum-tile-features=2500000"  # 2500K features per tile (down from 4M)
        ops[fo] += " -y highway -y name -y ref -y ref_type -y minzoom -y railway -y waterway -y width -y tunnel -y bridge -y natural -y landuse -y motorway_link_type -y route -y place -y population -y capital -y admin_level -y boundary"
        ops[fo] += " --hilbert"  # Use Hilbert curve for better compression
        ops[fo] += f" -L \"land:{l1_land}\""
        ops[fo] += f" -L \"water:{l1_water}\""
        ops[fo] += f" -L \"urban:{l1_urban}\""
        ops[fo] += f" -L \"forest:{l1_forest}\""
        # grass disabled — see bottom of file
        ops[fo] += f" -L \"railways:{l1_railways}\""
        ops[fo] += f" -L \"ferries:{l1_ferries}\""
        ops[fo] += f" -L \"roads:{l1_roads}\""
        ops[fo] += f" -L \"places:{l1_places}\""
        ops[fo] += f" -L \"boundaries:{l1_borders}\""
        ops[fo] += f" -L \"highway_labels:{l1_highway_labels}\""

        # L2 mbtiles — all features for Z11-16 (merged L3 into L2)
        # Includes all road types (motorway through pedestrian) with minzoom filtering
        # NO road simplification (tolerance = 0)
        fo = os.path.join(l2_output_dir, f"{tile_name}.mbtiles")
        ops[fo] = f"\"{tool}\" -o \"{fo}\""
        ops[fo] += " -Z11 -z16"  # Z11-16 merged
        ops[fo] += " --buffer=64"
        ops[fo] += " --maximum-tile-bytes=5000000"    # 5 MB limit — activates drop logic for dense tiles
        ops[fo] += " --maximum-tile-features=500000"  # 500K features per tile
        ops[fo] += " --drop-densest-as-needed"
        ops[fo] += " -y highway -y name -y ref -y ref_type -y minzoom -y railway -y waterway -y width -y tunnel -y bridge -y natural -y landuse -y motorway_link_type -y route -y place -y population -y capital -y admin_level -y boundary"
        ops[fo] += " --hilbert"  # Use Hilbert curve for better compression
        ops[fo] += f" -L \"land:{l2_land}\""
        ops[fo] += f" -L \"water:{l2_water}\""
        ops[fo] += f" -L \"urban:{l2_urban}\""
        ops[fo] += f" -L \"forest:{l2_forest}\""
        ops[fo] += f" -L \"railways:{l2_railways}\""
        ops[fo] += f" -L \"ferries:{l2_ferries}\""
        ops[fo] += f" -L \"waterways:{l2_waterways}\""
        ops[fo] += f" -L \"roads:{l2_roads}\""
        ops[fo] += f" -L \"places:{l2_places}\""
        ops[fo] += f" -L \"boundaries:{l2_borders}\""
        ops[fo] += f" -L \"highway_labels:{l2_highway_labels}\""

        for fo, cmd in ops.items():
            if not os.path.exists(fo):
                print(f"    - {fo}")
                try:
                    subprocess.run(cmd, shell=True)
                except subprocess.CalledProcessError as ex:
                    print(ex)

    return True


def transform_minzoom_to_tippecanoe(geojson_path: str) -> None:
    """
    Transform minzoom from feature properties to tippecanoe object.

    Tippecanoe requires minzoom in the tippecanoe object, not properties:
    {"tippecanoe": {"minzoom": 3}, "properties": {"name": "..."}}
    """
    import json

    with open(geojson_path, 'r') as f:
        data = json.load(f)

    for feature in data.get('features', []):
        props = feature.get('properties', {})
        if 'minzoom' in props:
            # Move minzoom to tippecanoe object
            minzoom_value = props.pop('minzoom')
            if 'tippecanoe' not in feature:
                feature['tippecanoe'] = {}
            feature['tippecanoe']['minzoom'] = minzoom_value

    with open(geojson_path, 'w') as f:
        json.dump(data, f)


def archive_tiles(
        source_path: str,
        tar_path: str) -> bool:

    cmd = f"tar -cf \"{tar_path}\" -C \"{source_path}\" ."

    if os.path.exists(tar_path):
        print("    Archive exists, skipping")
        return True

    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    return True


# ===========================================================================
# COMMENTED OUT: Waterway / Water / Forest / Grass / Urban processing
#                + unused legacy functions
# Removed 2026-02-04 — restarting waterway implementation from scratch;
#                       forest/grass/urban also stripped (land-only + roads)
# ===========================================================================

# --- stream_geojson_features() (was: lines 7-122) ---
# [became unused when filter_waterways_by_width and filter_water_by_area were removed]
#
# def stream_geojson_features(input_path: str):
#     """Stream GeoJSON features one at a time for low memory processing.
#
#     Generator that yields individual GeoJSON Feature objects from a file
#     without loading the entire file into memory. Handles FeatureCollection
#     format and streams features incrementally.
#
#     Args:
#         input_path: Path to GeoJSON file
#
#     Yields:
#         dict: Individual GeoJSON Feature object
#
#     Memory usage: O(1) - constant regardless of file size
#     """
#     import json
#
#     buffer_size = 8192  # 8KB chunks
#     decoder = json.JSONDecoder()
#
#     with open(input_path, 'r') as f:
#         # Read initial buffer to find "features" array
#         buffer = f.read(buffer_size)
#         features_idx = buffer.find('"features"')
#
#         if features_idx == -1:
#             # Single Feature (not FeatureCollection)
#             f.seek(0)
#             content = f.read()
#             data = json.loads(content)
#             if data.get('type') == 'Feature':
#                 yield data
#             return
#
#         # Skip to start of features array
#         bracket_idx = buffer.find('[', features_idx)
#         if bracket_idx == -1:
#             # Read more to find the bracket
#             while bracket_idx == -1 and len(buffer) < 1000000:  # Safety limit
#                 chunk = f.read(buffer_size)
#                 if not chunk:
#                     break
#                 buffer += chunk
#                 bracket_idx = buffer.find('[', features_idx)
#
#         if bracket_idx == -1:
#             return  # No features array found
#
#         # Position file pointer after the opening bracket
#         f.seek(bracket_idx + 1)
#         buffer = f.read(buffer_size)
#
#         feature_count = 0
#         depth = 0
#         obj_start = 0
#         in_string = False
#         escape_next = False
#
#         while True:
#             i = 0
#             while i < len(buffer):
#                 char = buffer[i]
#
#                 if escape_next:
#                     escape_next = False
#                     i += 1
#                     continue
#
#                 if char == '\\':
#                     escape_next = True
#                     i += 1
#                     continue
#
#                 if char == '"':
#                     in_string = not in_string
#                     i += 1
#                     continue
#
#                 if in_string:
#                     i += 1
#                     continue
#
#                 if char == '{':
#                     if depth == 0:
#                         obj_start = i
#                     depth += 1
#                 elif char == '}':
#                     depth -= 1
#                     if depth == 0:
#                         # Complete object found
#                         obj_str = buffer[obj_start:i+1]
#                         try:
#                             feature = decoder.decode(obj_str)
#                             if feature.get('type') == 'Feature':
#                                 feature_count += 1
#                                 yield feature
#                         except json.JSONDecodeError:
#                             pass  # Skip malformed objects
#
#                         # Skip whitespace and comma after object
#                         i += 1
#                         while i < len(buffer) and buffer[i] in ' \t\n\r,':
#                             if buffer[i] == ']':
#                                 # End of features array
#                                 return
#                             i += 1
#                         continue
#
#                 i += 1
#
#             # Read more data
#             new_buffer = f.read(buffer_size)
#             if not new_buffer:
#                 break
#             buffer = buffer + new_buffer

# --- estimate_waterway_width() (was: lines 164-197) ---
#
# def estimate_waterway_width(waterway_type: str, name: str = None, tags: dict = None) -> float:
#     """Estimate waterway width in meters based on type and other tags.
#
#     Used for waterways that don't have explicit width tags (~60-70% of features).
#     Based on OSM statistics and typical widths.
#
#     Args:
#         waterway_type: The waterway type (river, canal, stream, etc.)
#         name: Optional name - named features are typically more important
#         tags: Optional dict of additional tags (intermittent, etc.)
#
#     Returns:
#         Estimated width in meters, or 0 if should be excluded
#     """
#     # Base estimates from OSM statistics
#     base_widths = {
#         'river': 15.0,    # 50% of rivers are >10m, average ~15m
#         'canal': 8.0,     # 26% are >10m, average ~8m
#         'stream': 2.0,    # Typically <3m, jumpable
#         'ditch': 1.5,     # 91-93% are <5m
#         'drain': 1.0,     # Small artificial drainage
#     }
#
#     base_width = base_widths.get(waterway_type, 5.0)
#
#     # Boost named features - names indicate importance
#     if name:
#         base_width *= 1.5
#
#     # Reduce intermittent features - seasonal/temporary
#     if tags and tags.get('intermittent') == 'yes':
#         base_width *= 0.5
#
#     return base_width

# --- filter_waterways_by_width() (was: lines 730-827) ---
#
# def filter_waterways_by_width(geojson_path: str, level: str) -> bool:
#     """Filter waterways by width after GeoJSON conversion.
#
#     Applies intelligent filtering based on waterway width and importance:
#     - L1 (Z7-10): Only major waterways >30m or named features
#     - L2 (Z11-13): Medium+ waterways >10m or named features
#     - L3 (Z14-16): Small+ waterways >5m or named features
#
#     Args:
#         geojson_path: Path to the waterways GeoJSON file
#         level: 'L1', 'L2', or 'L3'
#
#     Returns:
#         True if successful, False otherwise
#     """
#     import json
#
#     # Check if file exists and has content
#     if not os.path.exists(geojson_path):
#         return True  # File doesn't exist, nothing to filter
#
#     if os.path.getsize(geojson_path) < 100:
#         return True  # Empty file, nothing to filter
#
#     try:
#         # Width thresholds for each level
#         if level == 'L1':
#             width_threshold = 30.0  # Major rivers only
#         elif level == 'L2':
#             width_threshold = 10.0  # Medium rivers
#         elif level == 'L3':
#             width_threshold = 5.0   # Small streams and larger
#         else:
#             return True  # Unknown level, don't filter
#
#         # Stream processing for low memory usage
#         temp_output = f"{geojson_path}.tmp"
#         original_count = 0
#         filtered_count = 0
#
#         with open(temp_output, 'w') as out_f:
#             # Write header
#             out_f.write('{"type": "FeatureCollection", "features": [')
#
#             first_feature = True
#             for feature in stream_geojson_features(geojson_path):
#                 original_count += 1
#                 props = feature.get('properties', {})
#
#                 # Get width - either explicit or estimated
#                 width_str = props.get('width')
#                 waterway_type = props.get('waterway', 'river')
#                 name = props.get('name')
#
#                 # Skip tunnels (underground, not visible)
#                 if props.get('tunnel') == 'yes':
#                     continue
#
#                 # Skip intermittent at L1 (seasonal waterways clutter low zoom)
#                 if level == 'L1' and props.get('intermittent') == 'yes':
#                     continue
#
#                 if width_str:
#                     # Explicit width tag exists
#                     try:
#                         # Handle various formats: "15", "15.5", "15 m", etc.
#                         width = float(width_str.split()[0])
#                     except (ValueError, AttributeError):
#                         # Invalid width format, use heuristic
#                         width = estimate_waterway_width(waterway_type, name, props)
#                 else:
#                     # No width tag, estimate from type and name
#                     width = estimate_waterway_width(waterway_type, name, props)
#
#                 # Include if: width > threshold OR has a name (named features are important)
#                 if width > width_threshold or name:
#                     filtered_count += 1
#                     if not first_feature:
#                         out_f.write(',')
#                     first_feature = False
#                     json.dump(feature, out_f)
#
#             # Write footer
#             out_f.write(']}')
#
#         # Atomic replace
#         os.replace(temp_output, geojson_path)
#
#         if filtered_count < original_count:
#             print(f"      Filtered {original_count - filtered_count} small waterways (kept {filtered_count})")
#
#         return True
#
#     except Exception as e:
#         print(f"Error filtering waterways by width in {geojson_path}: {e}")
#         if os.path.exists(f"{geojson_path}.tmp"):
#             os.remove(f"{geojson_path}.tmp")
#         return False

# --- filter_water_by_area() (was: lines 830-920) ---
#
# def filter_water_by_area(geojson_path: str, level: str) -> bool:
#     """Filter water polygons by area at each zoom level.
#
#     Conservative thresholds (replaces simplification to prevent 51GB files):
#     - L1 (Z7-10): >10 km² (major lakes only - oceans, big lakes)
#     - L2 (Z11-13): >1 km² (medium+ lakes - most significant lakes)
#     - L3 (Z14-16): >0.1 km² (small+ lakes - ponds and larger)
#
#     Args:
#         geojson_path: Path to the water polygons GeoJSON file
#         level: 'L1', 'L2', or 'L3'
#
#     Returns:
#         True if successful, False otherwise
#     """
#     import json
#
#     # Check if file exists and has content
#     if not os.path.exists(geojson_path):
#         return True  # File doesn't exist, nothing to filter
#
#     if os.path.getsize(geojson_path) < 100:
#         return True  # Empty file, nothing to filter
#
#     try:
#         # Area thresholds in km² for each level (conservative approach)
#         if level == 'L1':
#             area_threshold_km2 = 10.0  # Major lakes only (>10 km²)
#         elif level == 'L2':
#             area_threshold_km2 = 1.0   # Medium+ lakes (>1 km²)
#         elif level == 'L3':
#             area_threshold_km2 = 0.1   # Small+ lakes (>0.1 km² = 100,000 m²)
#         else:
#             return True  # Unknown level, don't filter
#
#         # Convert km² to approximate degrees² (at equator, 1° ≈ 111km, so 1 km² ≈ 0.00008 deg²)
#         area_threshold_deg2 = area_threshold_km2 * 0.00008  # km² to degrees² at equator
#
#         # Stream processing for low memory usage
#         temp_output = f"{geojson_path}.tmp"
#         original_count = 0
#         filtered_count = 0
#
#         with open(temp_output, 'w') as out_f:
#             # Write header
#             out_f.write('{"type": "FeatureCollection", "features": [')
#
#             first_feature = True
#             for feature in stream_geojson_features(geojson_path):
#                 original_count += 1
#                 geom = feature.get('geometry', {})
#                 geom_type = geom.get('type', '')
#                 coords = geom.get('coordinates', [])
#
#                 # Calculate area using shoelace formula
#                 if geom_type == 'Polygon' and len(coords) > 0:
#                     area_deg2 = _calculate_polygon_area(coords[0])  # Exterior ring
#                 elif geom_type == 'MultiPolygon' and len(coords) > 0:
#                     # Sum areas of all polygons
#                     area_deg2 = 0
#                     for polygon in coords:
#                         if len(polygon) > 0:
#                             area_deg2 += _calculate_polygon_area(polygon[0])
#                 else:
#                     # Unknown geometry type, keep it to be safe
#                     area_deg2 = area_threshold_deg2 + 1
#
#                 # Include if area > threshold
#                 if area_deg2 >= area_threshold_deg2:
#                     filtered_count += 1
#                     if not first_feature:
#                         out_f.write(',')
#                     first_feature = False
#                     json.dump(feature, out_f)
#
#             # Write footer
#             out_f.write(']}')
#
#         # Atomic replace
#         os.replace(temp_output, geojson_path)
#
#         if filtered_count < original_count:
#             print(f"      Filtered {original_count - filtered_count} small water polygons (kept {filtered_count})")
#
#         return True
#
#     except Exception as e:
#         print(f"Error filtering water by area in {geojson_path}: {e}")
#         if os.path.exists(f"{geojson_path}.tmp"):
#             os.remove(f"{geojson_path}.tmp")
#         return False

# --- _calculate_polygon_area() (was: lines 923-944) ---
#
# def _calculate_polygon_area(coordinates: list) -> float:
#     """Calculate approximate area of a polygon using shoelace formula.
#
#     Args:
#         coordinates: List of [lon, lat] coordinate pairs
#
#     Returns:
#         Area in square degrees (approximate)
#     """
#     if len(coordinates) < 3:
#         return 0.0
#
#     # Shoelace formula for polygon area
#     area = 0.0
#     n = len(coordinates)
#
#     for i in range(n):
#         j = (i + 1) % n
#         area += coordinates[i][0] * coordinates[j][1]
#         area -= coordinates[j][0] * coordinates[i][1]
#
#     return abs(area) / 2.0

# --- process_single_tile(): export ops ---
#
# waterways_config = str(config_dir / "osmium-export-waterways.json")
#
# # WATER POLYGONS DISABLED - using waterways only to prevent canal/lake merging artifacts
# # L1 - Water (polygons) - DISABLED
# fi = os.path.join(source_l1_path, "water.pbf")
# fo = os.path.join(dest_l1_path, "water.geojson")
# ops[fo] = f"osmium export \"{fi}\" -f geojson -o \"{fo}\""
#
# # L2 - Water (polygons) - DISABLED
# fi = os.path.join(source_l1_path, "water.pbf")
# fo = os.path.join(dest_l2_path, "water.geojson")
# ops[fo] = f"osmium export \"{fi}\" -f geojson -o \"{fo}\""
#
# # L3 - Water (polygons) - DISABLED
# fi = os.path.join(source_l1_path, "water.pbf")
# fo = os.path.join(dest_l3_path, "water.geojson")
# ops[fo] = f"osmium export \"{fi}\" -f geojson -o \"{fo}\""
#
# # L1 - Forest
# fi = os.path.join(source_l1_path, "forest.pbf")
# fo = os.path.join(dest_l1_path, "forest.geojson")
# ops[fo] = f"osmium export \"{fi}\" -f geojson -o \"{fo}\""
#
# # L1 - Grass
# fi = os.path.join(source_l1_path, "grass.pbf")
# fo = os.path.join(dest_l1_path, "grass.geojson")
# ops[fo] = f"osmium export \"{fi}\" -f geojson -o \"{fo}\""
#
# # L1 - Urban
# fi = os.path.join(source_l1_path, "urban.pbf")
# fo = os.path.join(dest_l1_path, "urban.geojson")
# ops[fo] = f"osmium export \"{fi}\" -f geojson -o \"{fo}\""
#
# # L1 - Waterways
# fi = os.path.join(source_l1_path, "waterways.pbf")
# fo = os.path.join(dest_l1_path, "waterways.geojson")
# ops[fo] = f"osmium export \"{fi}\" -f geojson -c \"{waterways_config}\" -o \"{fo}\""
#
# # L2 - Waterways
# fi = os.path.join(source_l2_path, "waterways.pbf")
# fo = os.path.join(dest_l2_path, "waterways.geojson")
# ops[fo] = f"osmium export \"{fi}\" -f geojson -c \"{waterways_config}\" -o \"{fo}\""
#
# # L3 - Waterways
# fi = os.path.join(source_l3_path, "waterways.pbf")
# fo = os.path.join(dest_l3_path, "waterways.geojson")
# ops[fo] = f"osmium export \"{fi}\" -f geojson -c \"{waterways_config}\" -o \"{fo}\""

# --- process_single_tile(): parallel branch ---
#
# # waterways.geojson simplify branch:
# elif fo.endswith("waterways.geojson"):
#     file_type = 'waterways'
# # water.geojson simplify skip:
# elif fo.endswith("water.geojson"):
#     continue  # Skip water - filtered by area instead of simplified
#
# # STEP 2.5: Filter waterways by width (all levels: L1, L2, L3)
# waterway_files = [
#     (os.path.join(dest_l1_path, "waterways.geojson"), 'L1'),
#     (os.path.join(dest_l2_path, "waterways.geojson"), 'L2'),
#     (os.path.join(dest_l3_path, "waterways.geojson"), 'L3'),
# ]
# for ww_file, ww_level in waterway_files:
#     if os.path.exists(ww_file):
#         if not filter_waterways_by_width(ww_file, ww_level):
#             return (tile_id, False, f"Width filtering failed for {ww_file}")
#
# # STEP 2.6: Water area filtering - DISABLED (water polygons not generated)
# # water_files = [
# #     (os.path.join(dest_l1_path, "water.geojson"), 'L1'),
# #     (os.path.join(dest_l2_path, "water.geojson"), 'L2'),
# #     (os.path.join(dest_l3_path, "water.geojson"), 'L3'),
# # ]
# # for water_file, water_level in water_files:
# #     if os.path.exists(water_file):
# #         if not filter_water_by_area(water_file, water_level):
# #             return (tile_id, False, f"Area filtering failed for {water_file}")
#
# # waterways.geojson zorder branch:
# elif fo.endswith("waterways.geojson"):
#     zorder_type = 'waterways'

# --- process_single_tile(): sequential branch ---
#
# # waterways.geojson simplify branch:
# elif fo.endswith("waterways.geojson"):
#     file_type = 'waterways'
# # water.geojson simplify skip:
# elif fo.endswith("water.geojson"):
#     continue  # Skip water - filtered by area instead of simplified
#
# # STEP 2.5: Filter waterways by width
# print(f"\n  STEP 2.5: Filtering waterways by width")
# waterway_files = [
#     (os.path.join(dest_l1_path, "waterways.geojson"), 'L1'),
#     (os.path.join(dest_l2_path, "waterways.geojson"), 'L2'),
#     (os.path.join(dest_l3_path, "waterways.geojson"), 'L3'),
# ]
# # Map levels to thresholds for display
# threshold_map = {'L1': '30m', 'L2': '10m', 'L3': '5m'}
# for ww_file, ww_level in waterway_files:
#     if os.path.exists(ww_file):
#         print(f"    Filtering {os.path.basename(ww_file)} ({ww_level}, threshold: {threshold_map[ww_level]})")
#         if not filter_waterways_by_width(ww_file, ww_level):
#             error_msg = f"Width filtering failed for {ww_file}"
#             return (tile_id, False, error_msg)
#         print(f"    ✓ Done")
#
# # STEP 2.6: Water area filtering - DISABLED (water polygons not generated)
# # print(f"\n  STEP 2.6: Filtering water polygons by area")
# # water_files = [
# #     (os.path.join(dest_l1_path, "water.geojson"), 'L1'),
# #     (os.path.join(dest_l2_path, "water.geojson"), 'L2'),
# #     (os.path.join(dest_l3_path, "water.geojson"), 'L3'),
# # ]
# # area_threshold_map = {'L1': '>10km²', 'L2': '>1km²', 'L3': '>0.1km²'}
# # for water_file, water_level in water_files:
# #     if os.path.exists(water_file):
# #         print(f"    Filtering {os.path.basename(water_file)} ({water_level}, threshold: {area_threshold_map[water_level]})")
# #         if not filter_water_by_area(water_file, water_level):
# #             error_msg = f"Area filtering failed for {water_file}"
# #             return (tile_id, False, error_msg)
# #         print(f"    ✓ Done")
#
# # waterways.geojson zorder branch:
# elif fo.endswith("waterways.geojson"):
#     zorder_type = 'waterways'

# --- build_mbtiles(): variables and tippecanoe layers ---
#
# # l1_water = os.path.join(l1_base_dir, "water.geojson")  # DISABLED - waterways only
# l1_forest = os.path.join(l1_base_dir, "forest.geojson")
# l1_grass = os.path.join(l1_base_dir, "grass.geojson")
# l1_urban = os.path.join(l1_base_dir, "urban.geojson")
# l1_waterways = os.path.join(l1_base_dir, "waterways.geojson")
#
# # l2_water = os.path.join(l2_base_dir, "water.geojson")  # DISABLED - waterways only
# l2_waterways = os.path.join(l2_base_dir, "waterways.geojson")
#
# # l3_water = os.path.join(l3_base_dir, "water.geojson")  # DISABLED - waterways only
# l3_waterways = os.path.join(l3_base_dir, "waterways.geojson")
#
# # L1 tippecanoe -y flags (removed from active):
# # -y waterway -y width -y intermittent -y tunnel
#
# # L1 tippecanoe water/background/waterway layers (removed from active):
# # WATER POLYGONS DISABLED - prevents canal/lake merging artifacts
# # ops[fo] += f" -L water:{l1_water}"
# # ops[fo] += " --coalesce"
# ops[fo] += f" -L forest:{l1_forest}"
# ops[fo] += f" -L grass:{l1_grass}"
# ops[fo] += f" -L urban:{l1_urban}"
# ops[fo] += f" -L waterways:{l1_waterways}"
#
# # L2 tippecanoe -y flags (removed from active):
# # -y waterway -y width -y intermittent -y tunnel
#
# # L2 tippecanoe water/waterway layers (removed from active):
# # WATER POLYGONS DISABLED - prevents canal/lake merging artifacts
# # ops[fo] += f" -L water:{l2_water}"
# # ops[fo] += " --coalesce"
# ops[fo] += f" -L waterways:{l2_waterways}"
#
# # L3 tippecanoe -y flags (removed from active):
# # -y waterway -y width -y intermittent -y tunnel
#
# # L3 tippecanoe water/waterway layers (removed from active):
# # WATER POLYGONS DISABLED - prevents canal/lake merging artifacts
# # ops[fo] += f" -L water:{l3_water}"
# # ops[fo] += " --coalesce"
# ops[fo] += f" -L waterways:{l3_waterways}"

# --- _extract_features() (was: lines 1366-1526) ---
# [never called anywhere — entirely dead legacy code]
#
# def _extract_features(osm_file: str) -> bool:
#     print(f"    - Extracting tile features from {osm_file}")
#
#     p = Path(osm_file)
#     base_dir = p.parent
#     file_name = p.name
#     out_dir = os.path.join(base_dir, file_name.replace(".osm.pbf", ""))
#
#     if os.path.exists(out_dir):
#         shutil.rmtree(out_dir)
#     os.makedirs(out_dir, exist_ok=True)
#
#     print("      -> Extracting roads")
#     roads_pbf_file = os.path.join(out_dir, "roads.pbf")
#     cmd = f"osmium tags-filter \"{osm_file}\" w/highway"
#     cmd += f" -o \"{roads_pbf_file}\" --overwrite"
#
#     try:
#         subprocess.run(cmd, shell=True, check=True)
#     except subprocess.CalledProcessError as ex:
#         print(ex)
#         return False
#
#     roads_geojson_file = os.path.join(out_dir, "roads.geojson")
#     cmd = f"osmium export \"{roads_pbf_file}\" -f geojson"
#     cmd += " --geometry-types=linestring"
#     cmd += " --add-unique-id=counter"
#     cmd += " --overwrite"
#     cmd += f" -o \"{roads_geojson_file}\""
#
#     try:
#         subprocess.run(cmd, shell=True, check=True)
#     except subprocess.CalledProcessError as ex:
#         print(ex)
#         return False
#
#     print("      -> Extracting road labels")
#     labels_pbf_file = os.path.join(out_dir, "road_labels.pbf")
#     cmd = f"osmium tags-filter \"{osm_file}\" w/highway,name"
#     cmd += f" -o \"{labels_pbf_file}\" --overwrite"
#
#     try:
#         subprocess.run(cmd, shell=True, check=True)
#     except subprocess.CalledProcessError as ex:
#         print(ex)
#         return False
#
#     labels_geojson_file = os.path.join(out_dir, "road_labels.geojson")
#     cmd = f"osmium export \"{labels_pbf_file}\" -f geojson"
#     cmd += " --geometry-types=linestring"
#     cmd += " --add-unique-id=counter"
#     cmd += " --overwrite"
#     cmd += f" -o \"{labels_geojson_file}\""
#
#     try:
#         subprocess.run(cmd, shell=True, check=True)
#     except subprocess.CalledProcessError as ex:
#         print(ex)
#         return False
#
#     print("      -> Extracting waterways")
#     water_pbf_file = os.path.join(out_dir, "water.pbf")
#     cmd = f"osmium tags-filter \"{osm_file}\""
#     cmd += " nwr/natural=water nwr/waterway"
#     cmd += f" -o \"{water_pbf_file}\" --overwrite"
#
#     try:
#         subprocess.run(cmd, shell=True, check=True)
#     except subprocess.CalledProcessError as ex:
#         print(ex)
#         return False
#
#     water_geojson_file = os.path.join(out_dir, "water.geojson")
#     cmd = f"osmium export \"{water_pbf_file}\" -f geojson"
#     cmd += " --add-unique-id=counter"
#     cmd += " --overwrite"
#     cmd += f" -o \"{water_geojson_file}\""
#
#     try:
#         subprocess.run(cmd, shell=True, check=True)
#     except subprocess.CalledProcessError as ex:
#         print(ex)
#         return False
#
#     print("      -> Extracting landcover")
#     landcover_pbf_file = os.path.join(out_dir, "landcover.pbf")
#     cmd = f"osmium tags-filter \"{osm_file}\""
#     cmd += " nwr/landuse=forest nwr/natural=wood"
#     cmd += " nwr/landuse=farmland nwr/natural=grassland"
#     cmd += f" -o \"{landcover_pbf_file}\" --overwrite"
#
#     try:
#         subprocess.run(cmd, shell=True, check=True)
#     except subprocess.CalledProcessError as ex:
#         print(ex)
#         return False
#
#     landcover_geojson_file = os.path.join(out_dir, "landcover.geojson")
#     cmd = f"osmium export \"{landcover_pbf_file}\" -f geojson"
#     cmd += " --add-unique-id=counter"
#     cmd += " --overwrite"
#     cmd += f" -o \"{landcover_geojson_file}\""
#
#     try:
#         subprocess.run(cmd, shell=True, check=True)
#     except subprocess.CalledProcessError as ex:
#         print(ex)
#         return False
#
#     print("      -> Extracting boundaries")
#     boundaries_pbf_file = os.path.join(out_dir, "boundaries.pbf")
#     cmd = f"osmium tags-filter \"{osm_file}\""
#     cmd += " r/boundary=administrative r/admin_level=2"
#     cmd += f" -o \"{boundaries_pbf_file}\" --overwrite"
#
#     try:
#         subprocess.run(cmd, shell=True, check=True)
#     except subprocess.CalledProcessError as ex:
#         print(ex)
#         return False
#
#     boundaries_geojson_file = os.path.join(out_dir, "boundaries.geojson")
#     cmd = f"osmium export \"{boundaries_pbf_file}\" -f geojson"
#     cmd += " --geometry-types=linestring"
#     cmd += " --add-unique-id=counter"
#     cmd += " --overwrite"
#     cmd += f" -o \"{boundaries_geojson_file}\""
#
#     try:
#         subprocess.run(cmd, shell=True, check=True)
#     except subprocess.CalledProcessError as ex:
#         print(ex)
#         return False
#
#     print("      -> Extracting POI (motorcycle focused)")
#     poi_pbf_file = os.path.join(out_dir, "poi_motor.pbf")
#     cmd = f"osmium tags-filter \"{osm_file}\""
#     cmd += " n/amenity=fuel n/amenity=parking"
#     cmd += " n/shop=motorcycle n/tourism=viewpoint"
#     cmd += f" -o \"{poi_pbf_file}\" --overwrite"
#
#     try:
#         subprocess.run(cmd, shell=True, check=True)
#     except subprocess.CalledProcessError as ex:
#         print(ex)
#         return False
#
#     poi_geojson_file = os.path.join(out_dir, "poi_motor.geojson")
#     cmd = f"osmium export \"{poi_pbf_file}\" -f geojson"
#     cmd += " --geometry-types=point"
#     cmd += " --add-unique-id=counter"
#     cmd += " --overwrite"
#     cmd += f" -o \"{poi_geojson_file}\""
#
#     try:
#         subprocess.run(cmd, shell=True, check=True)
#     except subprocess.CalledProcessError as ex:
#         print(ex)
#         return False
#
#     return True
