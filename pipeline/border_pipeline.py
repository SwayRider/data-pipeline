from .base_pipeline import BasePipeline
from .osm_funcs import outline, extract_polygon
from .border_crossing import detect_crossings
import os
import shutil


class BorderDataPipeline(BasePipeline):
    def __init__(self, args):
        super().__init__(args, "manifest-border.yml")

    def _clean(self) -> None:
        temp_dir = self.config.temp_dir()
        result_dir = self.config.result_dir()
        for path in [
            os.path.join(temp_dir, "border-areas"),
            os.path.join(result_dir, "borders"),
            os.path.join(result_dir, "border-crossings"),
        ]:
            if os.path.exists(path):
                shutil.rmtree(path)
        for region in self.config.regions():
            region_dir = os.path.join(temp_dir, region.name)
            if os.path.exists(region_dir):
                shutil.rmtree(region_dir)
        manifest = os.path.join(result_dir, "manifest-border.yml")
        if os.path.exists(manifest):
            os.remove(manifest)

    def run(self) -> bool:
        if self.manifest.is_closed():
            print("Pipeline already completed")
            return True

        # Check that prepare-source-data has been run for all regions
        required_files = []
        for region in self.config.regions():
            required_files.append(
                os.path.join(self.config.result_dir(), "osm", region.name + ".osm.pbf"))
            required_files.append(
                os.path.join(self.config.result_dir(), "osm", region.name + "-core.osm.pbf"))

        if not self._check_prerequisites(required_files):
            print("Prerequisites missing — run prepare-source-data first")
            return False

        if not self._extract_region_borders():
            print("Failed to extract region borders")
            return False
        if not self._create_region_border_areas():
            print("Failed to create region border areas")
            return False
        if not self._detect_border_crossings():
            print("Failed to detect border crossings")
            return False

        staging = os.path.join(self.config.result_dir(), "_border_staging")
        os.makedirs(staging, exist_ok=True)
        shutil.copytree(
            os.path.join(self.config.result_dir(), "borders"),
            os.path.join(staging, "contours"),
            dirs_exist_ok=True)
        shutil.copytree(
            os.path.join(self.config.result_dir(), "border-crossings"),
            os.path.join(staging, "border-crossings"),
            dirs_exist_ok=True)
        shutil.copy2(
            os.path.join(self.config.result_dir(), "manifest-border.yml"),
            os.path.join(staging, "manifest.yml"))

        if not self._package_output(staging, "border.tar.bz2"):
            print("Failed to package border output")
            shutil.rmtree(staging)
            return False

        shutil.rmtree(staging)

        print("Finishing up")
        self.manifest.mark_closed()
        self.manifest.save()
        print("Done")
        return True

    def _extract_region_borders(self) -> bool:
        print("- Extracting region borders")
        for region in self.config.regions():
            print(f"  - {region.name}")

            if self.manifest.contours_exists(region.name):
                print("    Skipping, already exists")
                continue

            input_dir = os.path.join(
                    self.config.result_dir(), "osm")
            output_dir = os.path.join(
                    self.config.result_dir(), "borders")
            temp_dir = os.path.join(
                    self.config.temp_dir(), region.name, "tmp")

            os.makedirs(temp_dir, exist_ok=True)
            os.makedirs(output_dir, exist_ok=True)

            input_file = region.name + "-core.osm.pbf"
            output_core_file = region.name + "-core.geojson"

            res = outline(
                    input_dir, input_file,
                    output_dir, output_core_file,
                    temp_dir)
            if not res:
                shutil.rmtree(temp_dir)
                return False

            input_file = region.name + ".osm.pbf"
            output_extended_file = region.name + "-extended.geojson"

            res = outline(
                    input_dir, input_file,
                    output_dir, output_extended_file,
                    temp_dir)
            if not res:
                shutil.rmtree(temp_dir)
                return False

            self.manifest.add_contours(
                    region.name,
                    output_dir,
                    output_core_file,
                    output_dir,
                    output_extended_file,
                    "contours",
                    output_core_file,
                    "contours",
                    output_extended_file)
            self.manifest.save()

            shutil.rmtree(temp_dir)
        return True

    def _create_region_border_areas(self) -> bool:
        print("- Creating region border areas")
        for br in self.config.border_regions():
            print(f"  - {br.name}")
            osm_input_dir = os.path.join(
                    self.config.result_dir(), "osm")
            poly_input_dir = self.config.border_polygons_dir()
            output_dir = os.path.join(
                    self.config.temp_dir(), "border-areas")

            osm_input_file = br.osm_files()[0]
            poly_input_file = br.poly_file()
            output_file = f"{br.name}.osm.pbf"

            os.makedirs(output_dir, exist_ok=True)

            res = extract_polygon(
                    osm_input_dir, osm_input_file,
                    output_dir, output_file,
                    poly_input_dir, poly_input_file)
            if not res:
                return False

        return True

    def _detect_border_crossings(self) -> bool:
        print("- Detecting border crossings")
        for br in self.config.border_regions():
            print(f"  - {br.name}")

            if self.manifest.border_crossings_exists(br.name):
                print("    Skipping, already exists")
                continue

            osm_input_dir = os.path.join(
                    self.config.temp_dir(), "border-areas")
            geojson_input_dir = os.path.join(
                    self.config.result_dir(), "borders")
            output_dir = os.path.join(
                    self.config.result_dir(), "border-crossings")

            osm_input_file = f"{br.name}.osm.pbf"
            geojson_files = br.core_region_border_files()
            output_file = f"{br.name}.csv"

            os.makedirs(output_dir, exist_ok=True)

            res = detect_crossings(
                    br.region_names()[0],
                    br.region_names()[1],
                    osm_input_dir,
                    osm_input_file,
                    geojson_input_dir,
                    geojson_files[0],
                    output_dir,
                    output_file)
            if not res:
                return False

            self.manifest.add_border_crossings(
                    br.name,
                    output_dir,
                    output_file,
                    "border-crossings",
                    output_file)
            self.manifest.save()

        return True
