import osmium
from shapely.geometry import Polygon, Point, box, LineString
from shapely.strtree import STRtree
from shapely import wkb
from .geojson import geojson_to_poly
import csv
import os


class BorderCrossing:
    def __init__(
            self,
            osm_id: int,
            osm_type: str,
            from_region: str,
            to_region: str,
            location: Point):
        self.osm_id = osm_id
        self.osm_type = osm_type
        self.from_region = from_region
        self.to_region = to_region
        self.location = location


class BorderCrossingHandler(osmium.SimpleHandler):
    def __init__(
            self,
            region_1_name: str,
            region_2_name: str,
            region_1_polygons: list[Polygon]):
        super().__init__()
        self.region_1_name = region_1_name
        self.region_2_name = region_2_name
        self.region_1_polygons = region_1_polygons
        self.region_1_polygons_index = STRtree(region_1_polygons)
        self.wkb_factory = osmium.geom.WKBFactory()
        self.valid_hightway_types = [
                "motorway", "motorway_link",
                "trunk", "trunk_link",
                "primary", "primary_link",
                "secondary", "secondary_link"]
        self.crossings = []

    def write_csv(
            self,
            output_dir: str,
            file_name: str) -> bool:
        os.makedirs(output_dir, exist_ok=True)
        file_path = os.path.join(output_dir, file_name)
        with open(file_path, "w") as f:
            writer = csv.writer(f)
            writer.writerow([
                    "osm_id",
                    "osm_type",
                    "from_region",
                    "to_region",
                    "lon",
                    "lat"])
            for crossing in self.crossings:
                writer.writerow([
                    crossing.osm_id,
                    crossing.osm_type,
                    crossing.from_region,
                    crossing.to_region,
                    crossing.location.x,
                    crossing.location.y])

        return True

    def way(self, w):
        filtered, highway_type = self._filter_way(w)
        if filtered:
            return

        candidate_indices, line = self._intersectino_candidates(w)
        if candidate_indices is None:
            return

        orientation = self._get_orientation(w)
        osm_id = w.id

        crossing_found, crossing_points = self._find_crossings(
                candidate_indices, line, osm_id, highway_type, orientation)
        if crossing_found:
            print(f"Found crossing for {w.id} @")
            for p in crossing_points:
                print(f"  Lat: {p.x}, Lon: {p.y}")

    def _filter_way(self, w) -> tuple[bool, str]:
        if "highway" not in w.tags:
            return (True, None)
        highway_type = w.tags.get("highway")
        if highway_type not in self.valid_hightway_types:
            return (True, highway_type)
        if len(w.nodes) < 2:
            return (True, highway_type)
        return (False, highway_type)

    def _intersectino_candidates(self, w) -> tuple[list[Point], LineString]:
        try:
            wkb_bytes = self.wkb_factory.create_linestring(w)
        except Exception as e:
            print(f"Skipping way {w.id}, nodes={len(w.nodes)}, error={e}")
            return (None, None)

        line = wkb.loads(wkb_bytes)
        minx, miny, maxx, maxy = line.bounds
        bbox = box(minx, miny, maxx, maxy)

        candiate_indices = self.region_1_polygons_index.query(bbox)
        if candiate_indices.size == 0:
            return (None, None)

        return (candiate_indices, line)

    def _get_orientation(self, w) -> str:
        orientation = None
        ow = w.tags.get("oneway")
        if ow in ["yes", "1", "true"]:
            orientation = "forward"
        if ow in ["-1", "reverse"]:
            orientation = "reverse"
        return orientation

    def _find_crossings(
            self,
            candidate_indices: list[int],
            line: LineString,
            osm_id: int,
            highway_type: str,
            orientation: str
            ) -> tuple[bool, list[Point]]:
        crossing_found = False
        crossing_points = []
        for index in candidate_indices:
            poly = self.region_1_polygons[index]

            boundary = poly.boundary
            intersection = boundary.intersection(line)

            if intersection.is_empty:
                continue

            found = False
            if intersection.geom_type == "Point":
                found = self._process_crossing(
                        osm_id, highway_type, orientation,
                        line, intersection, poly)
                if found:
                    crossing_found = True
                    crossing_points.append(intersection)
                continue

            if intersection.geom_type == "MultiPoint":
                for p in intersection.geoms:
                    found = self._process_crossing(
                            osm_id, highway_type, orientation,
                            line, p, poly)
                    if found:
                        crossing_found = True
                        crossing_points.append(p)
                continue

        return (crossing_found, crossing_points)

    def _process_crossing(
            self,
            osm_id: int,
            highway_type: str,
            orientation: str,
            line: LineString,
            intersection: Point,
            poly: Polygon) -> bool:
        dist = line.project(intersection)
        before = line.interpolate(max(dist - 1e-5, 0))
        after = line.interpolate(min(dist + 1e-5, line.length))
        (ok, before_region, after_region) = self._check_crossing(
            before, after, poly)

        if ok:
            if orientation is None or orientation == "forward":
                self.crossings.append(BorderCrossing(
                    osm_id,
                    highway_type,
                    before_region,
                    after_region,
                    intersection
                ))
            if orientation is None or orientation == "reverse":
                self.crossings.append(BorderCrossing(
                    osm_id,
                    highway_type,
                    after_region,
                    before_region,
                    intersection
                ))
        return ok

    def _check_crossing(
            self,
            before: Point,
            after: Point,
            poly: Polygon) -> tuple[bool, str, str]:

        before_region = ""
        after_region = ""

        if poly.contains(before):
            before_region = self.region_1_name
        if poly.contains(after):
            after_region = self.region_1_name

        if before_region == after_region:
            return (False, "", "")

        search = before
        if after_region == "":
            search = after

        outside = True
        for p in self.region_1_polygons:
            if p.contains(search):
                outside = False
                break

        if outside:
            if before_region == "":
                before_region = self.region_2_name
            if after_region == "":
                after_region = self.region_2_name

        if before_region != "" and after_region != "":
            return (True, before_region, after_region)

        return (False, "", "")


def detect_crossings(
        region_1_name: str,
        region_2_name: str,
        osm_input_dir: str,
        osm_input_file: str,
        geojson_input_dir: str,
        geojson_1_file: str,
        output_dir: str,
        output_file: str) -> bool:

    osm_file = os.path.join(osm_input_dir, osm_input_file)
    geojson_1_file = os.path.join(geojson_input_dir, geojson_1_file)
    out_file = os.path.join(output_dir, output_file)

    if os.path.exists(out_file):
        print(f"  - Skipping, already exists: {out_file}")
        return True

    region_1_polygons = geojson_to_poly(geojson_1_file)
    handler = BorderCrossingHandler(
            region_1_name,
            region_2_name,
            region_1_polygons)
    handler.apply_file(osm_file, locations=True)

    return handler.write_csv(output_dir, output_file)
