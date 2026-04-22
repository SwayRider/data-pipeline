import json
from shapely.geometry import Polygon, shape
import subprocess
import os


def geojson_to_poly(file) -> list[Polygon]:
    with open(file) as f:
        gj = json.load(f)

    polygons = []
    for feature in gj["features"]:
        geom = shape(feature["geometry"])
        polygons.append(geom)

    return polygons


def shape_to_geojson(
        shape_file: str,
        geojson_file: str,
        ogr_tool: str) -> bool:

    if os.path.exists(geojson_file):
        print(f"File '{geojson_file}' already exists. Skipping conversion.")
        return True

    cmd = f"\"{ogr_tool}\" -f GeoJSON \"{geojson_file}\" \"{shape_file}\""

    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    return True
