from .base_pipeline import BasePipeline
from .valhalla_funcs import cleanup_valhalla_data, create_valhalla_config
from .valhalla_funcs import build_valhalla_tiles, export_valhalla_edges
import os
import shutil


class ValhallaDataPipeline(BasePipeline):
    def __init__(self, args):
        super().__init__(args, "manifest-valhalla.yml")

    def _clean(self) -> None:
        temp_dir = self.config.temp_dir()
        result_dir = self.config.result_dir()
        for path in [
            os.path.join(temp_dir, "valhalla-build"),
            os.path.join(result_dir, "valhalla"),
        ]:
            if os.path.exists(path):
                shutil.rmtree(path)
        manifest = os.path.join(result_dir, "manifest-valhalla.yml")
        if os.path.exists(manifest):
            os.remove(manifest)

    def run(self) -> bool:
        if not self._prepare_tools(["valhalla"]):
            return False

        required_files = [
            os.path.join(self.config.result_dir(), "osm", region.name + ".osm.pbf")
            for region in self.config.regions()
        ]
        if not self._check_prerequisites(required_files):
            print("Prerequisites missing: OSM PBF files not found — run prepare-source-data first")
            return False

        srtm_dir = os.path.join(self.config.download_dir(), "srtm")
        if not os.path.isdir(srtm_dir) or not os.listdir(srtm_dir):
            print("Prerequisites missing: SRTM data not found — run prepare-source-data first")
            return False

        if not self._create_valhalla_data():
            return False

        if not self._package_output(
                os.path.join(self.config.result_dir(), "valhalla"),
                "valhalla.tar.bz2"):
            return False

        print("Finishing up")
        self.manifest.mark_closed()
        self.manifest.save()
        print("Done")

        return True

    def _create_valhalla_data(self) -> bool:
        print("- Creating valhalla data")
        build_path = os.path.join(
                self.config.temp_dir(), "valhalla-build")
        tools_path = os.path.join(
                self.tools["valhalla"]["valhalla"], "build")
        srtm_path = os.path.join(
                self.config.download_dir(), "srtm")
        osm_input_dir = os.path.join(
                self.config.result_dir(), "osm")

        os.makedirs(build_path, exist_ok=True)

        for region in self.config.regions():
            print(f"  - {region.name}")

            if self.manifest.valhalla_data_exists(region.name):
                print("    Skipping tiles, already exists")
                print("    Exporting polylines (for pelias)")
                res = export_valhalla_edges(
                        region.name,
                        tools_path,
                        build_path,
                        srtm_path,
                        self.config.result_dir())
                if not res:
                    return False
                continue

            print("    Cleaning old data")
            res = cleanup_valhalla_data(build_path)
            if not res:
                return False

            print("    Generating valhalla.json config file")
            res = create_valhalla_config(
                    build_path,
                    tools_path,
                    srtm_path)
            if not res:
                return False

            print("    Building tiles")
            res, output_path, tiles, admin, tz = build_valhalla_tiles(
                    region.name,
                    build_path,
                    tools_path,
                    srtm_path,
                    osm_input_dir,
                    f"{region.name}.osm.pbf",
                    self.config.result_dir())
            if not res:
                return False

            self.manifest.add_valhalla_data(
                    region.name,
                    output_path,
                    admin,
                    tz,
                    tiles,
                    f"valhalla/{region.name}",
                    "admin.sqlite",
                    "tz_world.sqlite",
                    "tiles.tar")
            self.manifest.save()

            print("    Exporting polylines (for pelias)")
            res = export_valhalla_edges(
                    region.name,
                    tools_path,
                    build_path,
                    self.config.result_dir())
            if not res:
                return False

        return True
