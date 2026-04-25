#!/usr/bin/env python3
"""
Generate overlap and border .poly files from Natural Earth country boundaries.

Usage:
    python3 gis/generate_polygons.py \\
        --config  config/config.yml \\
        --ne-dir  /path/to/download/natural-earth

Outputs:
    gis/export/overlap/overlap_{region}.poly   — core region buffered 100 km
    gis/export/borders/br_{A}_{B}.poly         — 10 km buffer of A ∩ 10 km buffer of B
"""

import argparse
import os
import sys
import warnings

import geopandas as gpd
import yaml
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

OVERLAP_BUFFER_M = 100_000  # 100 km
BORDER_BUFFER_M = 10_000    # 10 km
# EPSG:3857 gives metric distances; distortion at high latitudes is acceptable
# for the approximate clip regions these polygons define.
CRS_METRIC = "EPSG:3857"


def load_ne_countries(ne_dir: str) -> dict:
    shp = os.path.join(ne_dir, "ne_10m_admin_0_countries.shp")
    if not os.path.exists(shp):
        sys.exit(f"Natural Earth shapefile not found: {shp}")
    gdf = gpd.read_file(shp)
    result = {}
    for _, row in gdf.iterrows():
        iso = str(row.get("ISO_A2", "") or "").strip().lower()
        if iso and iso != "-99":
            result[iso] = row.geometry
    return result


def fill_holes(geom):
    if geom.geom_type == "Polygon":
        return Polygon(geom.exterior)
    if geom.geom_type == "MultiPolygon":
        return MultiPolygon([Polygon(p.exterior) for p in geom.geoms])
    return geom


def only_polygons(geom):
    """Drop non-polygon parts that can appear at intersection boundaries."""
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    if geom.geom_type == "GeometryCollection":
        parts = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        return unary_union(parts) if parts else geom.__class__()
    return geom


def buffer_m(geom, metres: int):
    gdf = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
    buffered = gdf.to_crs(CRS_METRIC).geometry.buffer(metres)
    return buffered.to_crs("EPSG:4326").iloc[0]


def geom_to_poly(name: str, geom) -> str:
    if geom.geom_type == "Polygon":
        polys = [geom]
    elif geom.geom_type == "MultiPolygon":
        polys = list(geom.geoms)
    else:
        raise ValueError(f"Cannot write geometry type {geom.geom_type} as .poly")

    lines = [name]
    section = 0
    for poly in polys:
        section += 1
        lines.append(str(section))
        for x, y in poly.exterior.coords:
            lines.append(f"    {x:.10f}     {y:.10f}")
        lines.append("END")
        for ring in poly.interiors:
            section += 1
            lines.append(f"!{section}")
            for x, y in ring.coords:
                lines.append(f"    {x:.10f}     {y:.10f}")
            lines.append("END")
    lines.append("END")
    return "\n".join(lines) + "\n"


def write_poly(path: str, name: str, geom):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(geom_to_poly(name, geom))
    print(f"  wrote {path}")


def build_core_geom(region_name: str, wof_codes: list, ne: dict):
    polys = []
    for code in wof_codes:
        code = str(code).lower()
        if code in ne:
            polys.append(ne[code])
        else:
            warnings.warn(
                f"[{region_name}] WOF code '{code}' not in Natural Earth — skipped"
            )
    if not polys:
        warnings.warn(f"[{region_name}] No geometries found — region skipped")
        return None
    return fill_holes(unary_union(polys))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="Path to config.yml")
    ap.add_argument("--ne-dir", required=True,
                    help="Directory containing ne_10m_admin_0_countries.shp")
    args = ap.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    overlap_out = os.path.join(script_dir, "export", "overlap")
    border_out = os.path.join(script_dir, "export", "borders")

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    ne = load_ne_countries(args.ne_dir)
    print(f"Loaded {len(ne)} countries from Natural Earth\n")

    region_geoms = {}

    print("--- Overlap polygons (100 km buffer per region) ---")
    for entry in cfg["regions"]:
        region_name = next(iter(entry))
        wof_codes = entry[region_name].get("core", {}).get("wof", [])

        core_geom = build_core_geom(region_name, wof_codes, ne)
        if core_geom is None:
            continue
        region_geoms[region_name] = core_geom

        buf = fill_holes(buffer_m(core_geom, OVERLAP_BUFFER_M))
        fname = f"overlap_{region_name}.poly"
        write_poly(os.path.join(overlap_out, fname), fname[:-5], buf)

    print("\n--- Border polygons (intersection of 10 km buffers) ---")
    for br in cfg.get("border-regions", []):
        r1, r2 = br
        name = f"{r1}_{r2}"

        if r1 not in region_geoms or r2 not in region_geoms:
            warnings.warn(f"Border '{name}': missing geometry for '{r1}' or '{r2}' — skipped")
            continue

        buf1 = buffer_m(region_geoms[r1], BORDER_BUFFER_M)
        buf2 = buffer_m(region_geoms[r2], BORDER_BUFFER_M)
        zone = only_polygons(buf1.intersection(buf2))

        if zone.is_empty:
            warnings.warn(f"Border '{name}': intersection is empty — skipped")
            continue

        zone = fill_holes(zone)
        fname = f"br_{name}.poly"
        write_poly(os.path.join(border_out, fname), name, zone)

    print("\nDone.")


if __name__ == "__main__":
    main()
