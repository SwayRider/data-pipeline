import os
import subprocess
import geopandas as gpd
import shutil
from shapely.ops import unary_union


def extract_polygons(
        source_path: str,
        source_file: str,
        destination_path: str,
        destination_file: str,
        polygon_path: str,
        polygon_files: list[str],
        temp_dir: str) -> bool:

    if len(polygon_files) == 1:
        return extract_polygon(
                source_path, source_file,
                destination_path, destination_file,
                polygon_path, polygon_files[0])

    output_file = os.path.join(destination_path, destination_file)
    if os.path.exists(output_file):
        print(f"File '{destination_file}' already exists. Skipping extract.")
        return True

    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(destination_path, exist_ok=True)

    tmp_files = []
    for polygon_file in polygon_files:
        tmp_file = f"tmp-{len(tmp_files)}.osm.pbf"
        tmp_files.append(tmp_file)
        res = extract_polygon(
                source_path, source_file,
                temp_dir, tmp_file,
                polygon_path, polygon_file)
        if not res:
            return False

    res = merge_osm_files(
            temp_dir, tmp_files,
            destination_path, destination_file)

    for tmp_file in tmp_files:
        os.remove(os.path.join(temp_dir, tmp_file))

    if not res:
        return False

    return True


def extract_tiles(
        source_path: str,
        source_files: list[str],
        destination_path: str,
        min_lat: int,
        max_lat: int,
        min_lon: int,
        max_lon: int,
        tile_size: int,
        gen_tile: str = None) -> bool:

    os.makedirs(destination_path, exist_ok=True)

    # If gen_tile specified, filter to only that tile
    if gen_tile:
        try:
            from .tiles import parse_tile_id
            target_lat, target_lon, _ = parse_tile_id(gen_tile)

            # Validate tile is within configured region
            if not (min_lat <= target_lat < max_lat and min_lon <= target_lon < max_lon):
                print(f"ERROR: Tile {gen_tile} outside configured region")
                return False

            print(f"==> Extracting only tile: {gen_tile}")

            # Narrow loop bounds to process only target tile
            min_lat = target_lat
            max_lat = target_lat + tile_size
            min_lon = target_lon
            max_lon = target_lon + tile_size

        except ValueError as e:
            print(f"ERROR: Invalid tile ID '{gen_tile}': {e}")
            return False

    # Existing loop continues with potentially narrowed bounds
    for lat in range(min_lat, max_lat, tile_size):
        lat_id = f"S{-lat:02d}" if lat < 0 else f"N{lat:02d}"
        lat2 = lat + tile_size
        for lon in range(min_lon, max_lon, tile_size):
            lon_id = f"W{-lon:03d}" if lon < 0 else f"E{lon:03d}"
            lon2 = lon + tile_size

            tile_id = f"{lat_id}_{lon_id}"

            base_output = os.path.join(destination_path, tile_id)
            final_output_file = f"{base_output}.osm.pbf"

            if os.path.exists(final_output_file):
                print(f"File '{final_output_file}' already exists. Skipping extract.")
                continue

            part = 0
            for sf in source_files:
                part += 1
                source = os.path.join(source_path, sf)

                suffix = f"-{part:02d}"
                output_file = f"{base_output}{suffix}.osm.pbf"

                print(f"  - {output_file}")

                cmd = "osmium extract"
                cmd += f" --bbox {lon},{lat},{lon2},{lat2}"
                cmd += " --strategy complete_ways --overwrite"
                cmd += f" -o \"{output_file}\""
                cmd += f" \"{source}\""

                try:
                    subprocess.run(cmd, shell=True, check=True)
                except subprocess.CalledProcessError as ex:
                    print(ex)
                    return False

            if part == 1:
                os.rename(output_file, final_output_file)
            elif part > 1:
                in_files = [f"{base_output}-{p:02d}.osm.pbf"
                            for p in range(1, part + 1)]

                cmd = "osmium merge"
                cmd += " " + " ".join(f'"{f}"' for f in in_files)
                cmd += f" -o \"{final_output_file}\""

                try:
                    subprocess.run(cmd, shell=True, check=True)
                except subprocess.CalledProcessError as ex:
                    print(ex)
                    return False

    return True


def filter_L1_L2(
        source_path: str,
        min_lat: int,
        max_lat: int,
        min_lon: int,
        max_lon: int,
        tile_size: int,
        gen_tile: str = None,
        with_service_roads: bool = False) -> bool:

    # If gen_tile specified, filter to only that tile
    if gen_tile:
        try:
            from .tiles import parse_tile_id
            target_lat, target_lon, _ = parse_tile_id(gen_tile)

            # Only process if this tile is in the range
            if not (min_lat <= target_lat < max_lat and min_lon <= target_lon < max_lon):
                print(f"ERROR: Tile {gen_tile} outside configured region")
                return False

            print(f"==> Filtering L1/L2 for single tile: {gen_tile}")
            min_lat = target_lat
            max_lat = target_lat + tile_size
            min_lon = target_lon
            max_lon = target_lon + tile_size

        except ValueError as e:
            print(f"ERROR: {e}")
            return False

    for lat in range(min_lat, max_lat, tile_size):
        lat_id = f"S{-lat:02d}" if lat < 0 else f"N{lat:02d}"
        for lon in range(min_lon, max_lon, tile_size):
            lon_id = f"W{-lon:03d}" if lon < 0 else f"E{lon:03d}"
            tile_id = f"{lat_id}_{lon_id}"

            source_file = os.path.join(source_path, f"{tile_id}.osm.pbf")
            target_l1_dir = os.path.join(source_path, tile_id, "L1", "filtered")
            target_l2_dir = os.path.join(source_path, tile_id, "L2", "filtered")

            os.makedirs(target_l1_dir, exist_ok=True)
            os.makedirs(target_l2_dir, exist_ok=True)

            ops = {}

            # L1 - Roads
            fl = os.path.join(target_l1_dir, "roads.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/highway=motorway w/highway=trunk w/highway=primary w/highway=secondary w/highway=tertiary"
            ops[fl] += " w/highway=motorway_link w/highway=trunk_link w/highway=primary_link w/highway=secondary_link w/highway=tertiary_link"
            ops[fl] += f" -o \"{fl}\""

            # L1 - Railways (basic types only)
            fl = os.path.join(target_l1_dir, "railways.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/railway=rail w/railway=light_rail w/railway=subway"
            ops[fl] += f" -o \"{fl}\""

            # L1 - Waterways (rivers and canals)
            fl = os.path.join(target_l1_dir, "waterways.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/waterway=river w/waterway=canal"
            ops[fl] += f" -o \"{fl}\""

            # L1 - Water (polygons)
            fl = os.path.join(target_l1_dir, "water.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/natural=water w/waterway=riverbank"
            ops[fl] += " r/natural=water r/waterway=riverbank"
            ops[fl] += f" -o \"{fl}\""

            # L1 - Urban
            fl = os.path.join(target_l1_dir, "urban.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/landuse=residential w/landuse=commercial w/landuse=industrial"
            ops[fl] += " r/landuse=residential r/landuse=commercial r/landuse=industrial"
            ops[fl] += f" -o \"{fl}\""

            # L1 - Forest
            fl = os.path.join(target_l1_dir, "forest.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/landuse=forest w/natural=wood"
            ops[fl] += " r/landuse=forest r/natural=wood"
            ops[fl] += f" -o \"{fl}\""

            # L2 - Roads
            fl = os.path.join(target_l2_dir, "roads.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/highway=motorway w/highway=trunk w/highway=primary"
            ops[fl] += " w/highway=secondary w/highway=tertiary"
            ops[fl] += " w/highway=unclassified w/highway=residential"
            ops[fl] += " w/highway=living_street"
            if with_service_roads:
                ops[fl] += " w/highway=service"
            ops[fl] += " w/highway=*link"
            ops[fl] += f" -o \"{fl}\""

            # L2 - Railways (basic types only)
            fl = os.path.join(target_l2_dir, "railways.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/railway=rail w/railway=light_rail w/railway=subway"
            ops[fl] += f" -o \"{fl}\""

            # L1 - Ferries (route=ferry)
            fl = os.path.join(target_l1_dir, "ferries.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/route=ferry r/route=ferry"
            ops[fl] += f" -o \"{fl}\""

            # L2 - Ferries (route=ferry)
            fl = os.path.join(target_l2_dir, "ferries.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/route=ferry r/route=ferry"
            ops[fl] += f" -o \"{fl}\""

            # L2 - Waterways (rivers and canals)
            fl = os.path.join(target_l2_dir, "waterways.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/waterway=river w/waterway=canal"
            ops[fl] += f" -o \"{fl}\""

            # L2 - Urban
            fl = os.path.join(target_l2_dir, "urban.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/landuse=residential w/landuse=commercial w/landuse=industrial"
            ops[fl] += " r/landuse=residential r/landuse=commercial r/landuse=industrial"
            ops[fl] += f" -o \"{fl}\""

            # L2 - Forest
            fl = os.path.join(target_l2_dir, "forest.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " w/landuse=forest w/natural=wood"
            ops[fl] += " r/landuse=forest r/natural=wood"
            ops[fl] += f" -o \"{fl}\""

            # L1 - Places (nodes + polygon relations for settlement boundaries)
            fl = os.path.join(target_l1_dir, "places.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " n/place=city n/place=town n/place=village n/place=hamlet"
            ops[fl] += " n/place=suburb n/place=neighbourhood"
            ops[fl] += " r/place=city r/place=town r/place=village r/place=hamlet"
            ops[fl] += " r/place=suburb r/place=neighbourhood"
            ops[fl] += f" -o \"{fl}\""

            # L2 - Places (nodes + polygon relations for settlement boundaries)
            fl = os.path.join(target_l2_dir, "places.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " n/place=city n/place=town n/place=village n/place=hamlet"
            ops[fl] += " n/place=suburb n/place=neighbourhood"
            ops[fl] += " r/place=city r/place=town r/place=village r/place=hamlet"
            ops[fl] += " r/place=suburb r/place=neighbourhood"
            ops[fl] += f" -o \"{fl}\""

            # L1 - Borders (country borders only, admin_level=2)
            fl = os.path.join(target_l1_dir, "borders.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " r/admin_level=2"
            ops[fl] += f" -o \"{fl}\""

            # L2 - Borders (country borders only, admin_level=2)
            fl = os.path.join(target_l2_dir, "borders.pbf")
            ops[fl] = f"osmium tags-filter \"{source_file}\""
            ops[fl] += " r/admin_level=2"
            ops[fl] += f" -o \"{fl}\""

            for fl, cmd in ops.items():
                if not os.path.exists(fl):
                    print(f"  - {fl}")

                    try:
                        subprocess.run(cmd, shell=True, check=True,
                                       capture_output=True, text=True)
                    except subprocess.CalledProcessError as ex:
                        print(f"  ERROR: {cmd}")
                        print(f"  Exit code: {ex.returncode}")
                        if ex.stderr:
                            print(f"  Stderr: {ex.stderr}")
                        return False

    return True


def extract_polygon(
        source_path: str,
        source_file: str,
        destination_path: str,
        destination_file: str,
        polygon_path: str,
        polygon_file: str) -> bool:

    input_file = os.path.join(source_path, source_file)
    output_file = os.path.join(destination_path, destination_file)
    poly_file = os.path.join(polygon_path, polygon_file)

    os.makedirs(destination_path, exist_ok=True)

    if os.path.exists(output_file):
        print(f"File '{destination_file}' already exists. Skipping extract.")
        return True

    cmd = "osmium extract"
    cmd += f" -p \"{poly_file}\" \"{input_file}\""
    cmd += f" -o \"{output_file}\""

    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    return True


def merge_osm_files(
        source_path: str,
        source_files: list[str],
        destination_path: str,
        destination_file: str) -> bool:

    source_files = [os.path.join(source_path, s) for s in source_files]
    output_file = os.path.join(destination_path, destination_file)

    os.makedirs(destination_path, exist_ok=True)

    if os.path.exists(output_file):
        print(f"File '{destination_file}' already exists. Skipping merge.")
        return True

    temp_file = output_file + ".tmp.pbf"

    cmd = "osmium merge"
    cmd += " " + " ".join(f'"{f}"' for f in source_files)
    cmd += f" -o \"{temp_file}\""

    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    # Sort into .osh.pbf (history format) so osmium time-filter can deduplicate.
    # Geofabrik country extracts may have the same border object at different
    # versions depending on when each extract was generated. osmium merge keeps
    # both versions; time-filter with a far-future date retains only the latest.
    temp_sorted = output_file + ".sorted.tmp.osh.pbf"

    cmd = f"osmium sort \"{temp_file}\" -o \"{temp_sorted}\" --strategy=multipass"

    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        os.remove(temp_file)
        return False

    os.remove(temp_file)

    cmd = f"osmium time-filter \"{temp_sorted}\" 2099-01-01T00:00:00Z -o \"{output_file}\""
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        os.remove(temp_sorted)
        return False

    os.remove(temp_sorted)
    return True


def merge_osm_region(
        core_source_path: str,
        core_source_file: str,
        overlap_source_path: str,
        overlap_source_files: list[str],
        destination_path: str,
        destination_file: str) -> bool:

    core_source_file = os.path.join(core_source_path, core_source_file)
    overlap_source_files = [os.path.join(overlap_source_path, s)
                            for s in overlap_source_files]
    output_file = os.path.join(destination_path, destination_file)

    os.makedirs(destination_path, exist_ok=True)

    if os.path.exists(output_file):
        print(f"File '{destination_file}' already exists. Skipping merge.")
        return True

    temp_merged = output_file + ".merged.tmp.pbf"
    temp_sorted = output_file + ".sorted.tmp.osh.pbf"

    # Step 1: merge core + overlap files
    cmd = "osmium merge"
    cmd += f" \"{core_source_file}\""
    cmd += " " + " ".join(f'"{f}"' for f in overlap_source_files)
    cmd += f" -o \"{temp_merged}\""
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    # Step 2: sort into .osh.pbf (history format) for deduplication
    cmd = f"osmium sort \"{temp_merged}\" -o \"{temp_sorted}\" --strategy=multipass"
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        os.remove(temp_merged)
        return False
    os.remove(temp_merged)

    # Step 3: keep only the latest version of each object
    cmd = f"osmium time-filter \"{temp_sorted}\" 2099-01-01T00:00:00Z -o \"{output_file}\""
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        os.remove(temp_sorted)
        return False
    os.remove(temp_sorted)

    return True


def outline(
        input_path: str,
        input_file: str,
        output_path: str,
        output_file: str,
        temp_dir: str) -> bool:

    input_file = os.path.join(input_path, input_file)
    output_file = os.path.join(output_path, output_file)

    os.makedirs(output_path, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)

    if os.path.exists(output_file):
        print(f"File '{output_file}' already exists. Skipping outline.")
        return True

    temp_file_raw = os.path.join(temp_dir, "tmp-raw.osm.pbf")
    temp_file = os.path.join(temp_dir, "tmp.osm.pbf")
    cmd = "osmium tags-filter"
    cmd += f" \"{input_file}\" r/boundary=administrative r/admin_level=2"
    cmd += f" -o \"{temp_file_raw}\" --overwrite"

    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    cmd = f"osmium sort \"{temp_file_raw}\" -o \"{temp_file}\" --strategy=multipass --overwrite"

    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        os.remove(temp_file_raw)
        return False

    os.remove(temp_file_raw)

    temp_file_2 = os.path.join(temp_dir, "tmp.geojson")
    cmd = "osmium export"
    cmd += f" \"{temp_file}\""
    cmd += f" -o \"{temp_file_2}\""
    cmd += " --geometry-types=multipolygon --overwrite"

    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        os.remove(temp_file)
        return False

    gdf = gpd.read_file(temp_file_2)
    merged = unary_union(gdf.geometry)
    polygons = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    out = gpd.GeoDataFrame(geometry=polygons, crs="EPSG:4326")
    out.to_file(output_file, driver="GeoJSON")

    os.remove(temp_file)
    os.remove(temp_file_2)
    return True


# ===========================================================================
# COMMENTED OUT: Waterway / Water / Forest / Grass / Urban processing
# Removed 2026-02-04 — restarting waterway implementation from scratch;
#                       forest/grass/urban also stripped (land-only + roads)
# ===========================================================================

# --- estimate_waterway_width() (was: top of file) ---
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

# --- filter_L1_L3(): L1 Water polygons extraction ---
# # L1 - Water
# fl = os.path.join(target_l1_dir, "water.pbf")
# ops[fl] = f"osmium tags-filter \"{source_file}\""
# ops[fl] += " w/natural=water w/waterway=riverbank"
# ops[fl] += " r/natural=water r/waterway=riverbank"
# ops[fl] += f" -o \"{fl}\""

# --- filter_L1_L3(): L1 Forest extraction ---
# # L1 - Forest
# fl = os.path.join(target_l1_dir, "forest.pbf")
# ops[fl] = f"osmium tags-filter \"{source_file}\""
# ops[fl] += " w/landuse=forest w/natural=wood"
# ops[fl] += " r/landuse=forest r/natural=wood"
# ops[fl] += f" -o \"{fl}\""

# --- filter_L1_L3(): L1 Grass extraction ---
# # L1 - Grass
# fl = os.path.join(target_l1_dir, "grass.pbf")
# ops[fl] = f"osmium tags-filter \"{source_file}\""
# ops[fl] += " w/landuse=grass w/landuse=meadow w/leisure=park"
# ops[fl] += " r/landuse=grass r/landuse=meadow r/leisure=park"
# ops[fl] += f" -o \"{fl}\""

# --- filter_L1_L3(): L1 Urban extraction ---
# # L1 - Urban
# fl = os.path.join(target_l1_dir, "urban.pbf")
# ops[fl] = f"osmium tags-filter \"{source_file}\""
# ops[fl] += " w/landuse=residential w/landuse=industrial w/landuse=commercial"
# ops[fl] += " r/landuse=residential r/landuse=industrial r/landuse=commercial"
# ops[fl] += f" -o \"{fl}\""

# --- filter_L1_L3(): L1 Waterways extraction ---
# # L1 - Waterways (rivers and canals, width-based filtering applied later)
# # Include: width > 30m OR (named rivers/canals) OR (rivers without width tag)
# # Exclude: tunnel=yes, intermittent=yes
# fl = os.path.join(target_l1_dir, "waterways.pbf")
# ops[fl] = f"osmium tags-filter \"{source_file}\""
# ops[fl] += " w/waterway=river w/waterway=canal"
# ops[fl] += f" -o \"{fl}\""

# --- filter_L1_L3(): L2 Waterways extraction ---
# # L2 - Waterways (rivers and canals, width-based filtering applied later)
# # Include: width > 10m OR named features OR (rivers/canals without width tag)
# # Exclude: tunnel=yes (intermittent OK at L2+)
# fl = os.path.join(target_l2_dir, "waterways.pbf")
# ops[fl] = f"osmium tags-filter \"{source_file}\""
# ops[fl] += " w/waterway=river w/waterway=canal"
# ops[fl] += f" -o \"{fl}\""

# --- filter_L1_L3(): L3 Waterways extraction ---
# # L3 - Waterways (all types except tunnels)
# # Include: All waterway types (streams, ditches, etc.)
# # Exclude: tunnel=yes only
# fl = os.path.join(target_l3_dir, "waterways.pbf")
# ops[fl] = f"osmium tags-filter \"{source_file}\""
# ops[fl] += " w/waterway=*"
# ops[fl] += f" -o \"{fl}\""
