from .base_pipeline import BasePipeline
from concurrent.futures import ThreadPoolExecutor
from .download import download_file, s3_download_and_unpack_gz
from .osm_funcs import extract_polygons, merge_osm_files, merge_osm_region
import os
import shutil


class OsmPipeline(BasePipeline):
    def __init__(self, args):
        super().__init__(args, "manifest-osm.yml")

    def initialize(self) -> bool:
        return super().initialize()

    def _clean(self) -> None:
        temp_dir = self.config.temp_dir()
        result_dir = self.config.result_dir()
        for path in [
            os.path.join(temp_dir, "osm_extract"),
            os.path.join(result_dir, "osm"),
        ]:
            if os.path.exists(path):
                shutil.rmtree(path)
        for region in self.config.regions():
            region_dir = os.path.join(temp_dir, region.name)
            if os.path.exists(region_dir):
                shutil.rmtree(region_dir)
        for f in [
            os.path.join(result_dir, "manifest-osm.yml"),
            os.path.join(result_dir, "osm.tar.bz2"),
        ]:
            if os.path.exists(f):
                os.remove(f)

    def run(self) -> bool:
        if self.manifest.is_closed():
            print("OSM pipeline already completed")
            return True

        if not self._download_data():
            print("Failed to download data")
            return False
        if not self._create_overlap_extracts():
            print("Failed to create overlap extracts")
            return False
        if not self._create_region_osm_files():
            print("Failed to create region osm files")
            return False
        if not self._package_output(
                os.path.join(self.config.result_dir(), "osm"),
                "osm.tar.bz2"):
            print("Failed to package output")
            return False

        print("Finishing up")
        self.manifest.mark_closed()
        self.manifest.save()
        print("Done")
        return True

    def _download_data(self) -> bool:
        print("- Downloading data")
        res = self._download_osm_data()
        if not res:
            return False
        res = self._download_srtm_data()
        if not res:
            return False
        res = self._download_natural_earth_data()
        if not res:
            return False

        return True

    def _download_osm_data(self) -> bool:
        regions = self.config.regions()
        all_osm_urls = []
        for region in regions:
            all_osm_urls.extend(region.osm_urls())

        with ThreadPoolExecutor(max_workers=self.config.max_worders()) as pool:
            futures = [pool.submit(
                download_file,
                osm_url,
                os.path.join(self.config.download_dir(), "osm"),
                osm_file) for (osm_url, osm_file) in all_osm_urls]

            for f in futures:
                try:
                    f.result()
                except Exception as e:
                    print(f"Error downloading OSM file: {e}")
                    return False

        with ThreadPoolExecutor(max_workers=self.config.max_worders()) as pool:
            futures = [pool.submit(
                download_file,
                f"{self.config.osm_download_url()}/{osm_region}-latest.osm.pbf",
                os.path.join(self.config.download_dir(), "tiles-osm"),
                f"{osm_region}-latest.osm.pbf")
                for osm_region in self.config.tile_regions()]

            for f in futures:
                try:
                    f.result()
                except Exception as e:
                    print(f"Error downloading OSM file: {e}")
                    return False

        return True

    def _download_srtm_data(self) -> bool:
        regions = self.config.regions()
        all_srtm_urls = []
        for region in regions:
            all_srtm_urls.extend(region.srtm_urls())

        unified_urls = set()
        unified_urls.update(all_srtm_urls)

        with ThreadPoolExecutor(max_workers=self.config.max_worders()) as pool:
            futures = [pool.submit(
                s3_download_and_unpack_gz,
                srtm_url,
                os.path.join(self.config.download_dir(), "srtm"),
                gz_file,
                subdir,
                hgt_file
            ) for (srtm_url, gz_file, hgt_file, subdir) in unified_urls]

            for f in futures:
                try:
                    f.result()
                except Exception as e:
                    print(f"Error downloading SRTM file: {e}")
                    return False

        return True

    def _download_natural_earth_data(self) -> bool:
        files = self.config.natural_earth_files()
        all_urls = []
        for file in files:
            all_urls.append((
                f"{self.config.natural_earth_download_url()}{file}",
                os.path.basename(file)
            ))

        with ThreadPoolExecutor(max_workers=self.config.max_worders()) as pool:
            futures = [pool.submit(
                download_file,
                natural_earth_url,
                os.path.join(self.config.download_dir(), "natural-earth"),
                natural_earth_file
            ) for (natural_earth_url, natural_earth_file) in all_urls]

            for f in futures:
                try:
                    f.result()
                except Exception as e:
                    print(f"Error downloading natural earth file: {e}")
                    return False

        return True

    def _create_overlap_extracts(self) -> bool:
        print("- Creating overlap extracts")
        temp_dir = os.path.join(self.config.temp_dir(), "osm_extract")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)
        for region in self.config.regions():
            print(f"  - {region.name}")
            input_dir = os.path.join(
                    self.config.download_dir(), "osm")
            output_dir = os.path.join(
                    self.config.temp_dir(), region.name, "overlap")

            overlap_osm_files = region.overlap_source_files()
            overlap_polygon_files = region.overlap_polygon_files()

            for source_file in overlap_osm_files:
                print(f"    - {source_file}")
                res = extract_polygons(
                        input_dir, source_file,
                        output_dir, source_file,
                        self.config.overlap_polygons_dir(),
                        overlap_polygon_files,
                        temp_dir)
                if not res:
                    shutil.rmtree(temp_dir)
                    return False

        shutil.rmtree(temp_dir)
        return True

    def _create_region_osm_files(self) -> bool:
        print("- Creating region OSM files")
        for region in self.config.regions():
            print(f"  - {region.name}")

            if self.manifest.pbf_exists(region.name):
                print("    Skipping, already exists")
                continue

            input_core_dir = os.path.join(
                    self.config.download_dir(), "osm")
            input_overlap_dir = os.path.join(
                    self.config.temp_dir(), region.name, "overlap")
            core_output_dir = os.path.join(
                    self.config.temp_dir(), region.name)
            output_dir = os.path.join(
                    self.config.result_dir(), "osm")

            core_source_files = region.core_source_files()
            overlap_source_files = region.overlap_source_files()
            core_file = region.name + "-core.osm.pbf"
            output_file = region.name + ".osm.pbf"

            res = merge_osm_files(
                    input_core_dir, core_source_files,
                    core_output_dir, core_file)
            if not res:
                return False

            res = merge_osm_region(
                    core_output_dir, core_file,
                    input_overlap_dir, overlap_source_files,
                    output_dir, output_file)
            if not res:
                return False

            # Copy core file to result_dir/osm so downstream pipelines can access it
            core_result_file = os.path.join(output_dir, core_file)
            if not os.path.exists(core_result_file):
                shutil.copy2(os.path.join(core_output_dir, core_file), core_result_file)

            self.manifest.add_pbf(
                    region.name,
                    output_dir,
                    output_file,
                    "osm",
                    output_file)
            self.manifest.save()

        return True
