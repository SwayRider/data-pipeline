from .manifest import Manifest
from .config import Config
from .dirs import init_dirs, cleanup_dirs
from .download import download_file
from .osm_funcs import extract_tiles, filter_L1_L2
from .tiles import extract_L1_L2_features, build_mbtiles, archive_tiles
from .zip import unzip_file
from .geojson import shape_to_geojson
import json
import math
import os
import shutil
import subprocess
import geopandas as gpd


class TilesPipeline:
    def __init__(self, args):
        self.args = args
        self.config = Config(self.args.config)
        self.manifest = None
        self.tools = {}
        self.gen_tile = getattr(args, 'gen_tile', None)  # Store gen_tile if provided
        self.with_service_roads = getattr(args, 'with_service_roads', False)

    def initialize(self) -> bool:
        print("- Initializing tiles pipeline")
        tag = None

        if self.args.clean:
            self._clean()
        elif getattr(self.args, 'clean_all', False):
            cleanup_dirs(self.config)
        init_dirs(self.config)
        tag = self.args.tag if not self.args.tag == "" else None

        self.manifest = Manifest(
            path=str(self.config.result_dir()),
            tag=tag,
            path_prefix="tiles",
            filename="manifest-tiles.yml")

        self.manifest.save()

        return True

    def _clean(self) -> None:
        temp_dir = self.config.temp_dir()
        result_dir = self.config.result_dir()
        for path in [
            os.path.join(temp_dir, "tile_extract"),
            os.path.join(result_dir, "tiles"),
        ]:
            if os.path.exists(path):
                shutil.rmtree(path)
        manifest = os.path.join(result_dir, "manifest-tiles.yml")
        if os.path.exists(manifest):
            os.remove(manifest)

    def run(self) -> bool:
        if self.manifest.is_closed():
            print("Tiles pipeline already completed")
            #return True

        if not self.args.skip_build:
            if not self._prepare_tools():
                print("Failed to prepare tools")
                return False
            if not self._download_data():
                print("Failed to download data")
                return False
            if not self._create_tile_extracts():
                print("Failed to create tile extracts")
                return False

        if self.args.upload:
            print("Warning: --upload is deprecated and has no effect")

        print("Finishing up")
        self.manifest.mark_closed()
        self.manifest.save()
        print("Done")
        return True

    def _prepare_tools(self) -> bool:
        print("- Installing tools")
        repo_list = self.config.tippecanoe_repos()
        self.tools["tippecanoe"] = {}
        for repo in repo_list:
            source_path = repo.pull(self.config.tools_dir())
            self.tools["tippecanoe"][repo.name] = source_path
            install_ok = repo.install(self.config.tools_dir())
            if not install_ok:
                print("  Error: failed to install tippecanoe")
                return False

        repo_list = self.config.osgeo_repos()
        self.tools["osgeo"] = {}
        for repo in repo_list:
            source_path = repo.pull(self.config.tools_dir())
            self.tools["osgeo"][repo.name] = source_path
            install_ok = repo.install(self.config.tools_dir())
            if not install_ok:
                print("  Error: failed to install osgeo")
                return False

        return True

    def _download_data(self) -> bool:
        print("- Downloading data")
        if not self.args.skip_download:
            if not self._download_osm_tile_regions():
                return False
            if not self._download_natural_earth_data():
                return False
            if not self._download_osm_land_polygons():
                return False
        return True

    def _download_osm_tile_regions(self) -> bool:
        """
        Download large regional OSM files (e.g., europe-latest.osm.pbf)
        These are different from region-specific files used by main pipeline
        Downloads from tile_regions config sequentially
        Automatically skips files that already exist (via download_file utility)
        """
        for osm_region in self.config.tile_regions():
            try:
                download_file(
                    f"{self.config.osm_download_url()}/{osm_region}-latest.osm.pbf",
                    os.path.join(self.config.download_dir(), "tiles-osm"),
                    f"{osm_region}-latest.osm.pbf")
            except Exception as e:
                print(f"Error downloading OSM file: {e}")
                return False
        return True

    def _download_natural_earth_data(self) -> bool:
        """
        Download Natural Earth shapefiles for L0 generation
        Uses natural_earth config section
        Automatically skips files that already exist
        """
        files = self.config.natural_earth_files()
        all_urls = [(
            f"{self.config.natural_earth_download_url()}{file}",
            os.path.basename(file)
        ) for file in files]

        for (natural_earth_url, natural_earth_file) in all_urls:
            try:
                download_file(
                    natural_earth_url,
                    os.path.join(self.config.download_dir(), "natural-earth"),
                    natural_earth_file
                )
            except Exception as e:
                print(f"Error downloading natural earth file: {e}")
                return False
        return True

    def _download_osm_land_polygons(self) -> bool:
        """Download OSM land-polygons shapefile for L1-L2 land layer."""
        download_file(
            self.config.osm_land_polygons_url(),
            os.path.join(self.config.download_dir(), "osm-land"),
            "land-polygons.zip"
        )
        return True

    def _create_tile_extracts(self) -> bool:
        """
        Complete tile generation process:
        1. Extract 10° grid tiles from downloaded OSM data (extract_tiles)
        2. Filter features by zoom level L1/L2 (filter_L1_L2)
        3. Convert PBF to GeoJSON (extract_L1_L2_features)
        4. Generate MBTiles with tippecanoe (build_mbtiles)
        5. Create tar archive (archive_tiles)

        This is extracted from Pipeline._create_tile_extracts() lines 154-218
        """
        print("- Creating tile extracts")
        temp_dir = os.path.join(self.config.temp_dir(), "tile_extract")
        l0_temp_dir = os.path.join(temp_dir, "L0")
        out_dir = os.path.join(self.config.result_dir(), "tiles")
        ogr_tool = os.path.join(
                self.tools["osgeo"]["gdal"], "build", "apps", "ogr2ogr")
        tippecanoe_tool = os.path.join(
                self.tools["tippecanoe"]["tippecanoe"], "tippecanoe")

        os.makedirs(temp_dir, exist_ok=True)
        os.makedirs(l0_temp_dir, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)

        source_files = [f"{region}-latest.osm.pbf"
                       for region in self.config.tile_regions()]

        # STEP 1: Extract 10° grid tiles from OSM data
        [min_lon, min_lat, max_lon, max_lat] = self.config.region_size()
        if not extract_tiles(
                os.path.join(self.config.download_dir(), "tiles-osm"),
                source_files, temp_dir,
                min_lat, max_lat, min_lon, max_lon,
                self.config.tile_size(),
                gen_tile=self.gen_tile):
            return False

        # STEP 2: Convert Natural Earth shapefiles to GeoJSON for L0
        for ne_file in self.config.natural_earth_files():
            file_name = os.path.basename(ne_file)
            ne_path = os.path.join(
                    self.config.download_dir(), "natural-earth")
            full_file_path = os.path.join(ne_path, file_name)
            full_shape_file = os.path.join(
                    ne_path, file_name.replace(".zip", ".shp"))
            full_geojson_file = os.path.join(
                    l0_temp_dir, file_name.replace(".zip", ".geojson"))

            # Check if ZIP file exists, if not download it
            if not os.path.exists(full_file_path):
                print(f"File {full_file_path} not found, downloading...")
                from pipeline.download import download_file
                try:
                    download_file(
                        f"{self.config.natural_earth_download_url()}{ne_file}",
                        ne_path,
                        file_name
                    )
                except Exception as e:
                    raise FileNotFoundError(
                        f"Failed to download {ne_file}: {e}. "
                        f"L0.mbtiles may be incomplete. "
                        f"Please check download URL and network connection."
                    )

            print(f"Unzipping {full_file_path}")
            unzip_file(full_file_path, ne_path)

            print(f"Converting {file_name} to geojson")
            shape_to_geojson(full_shape_file, full_geojson_file, ogr_tool)

            # Convert field names to lowercase for admin_0_countries (country boundaries)
            # Natural Earth provides UPPERCASE names (NAME_EN, NAME_NL, etc.)
            # but viewer expects lowercase (name_en, name_nl, etc.)
            if "admin_0_countries" in file_name:
                print(f"Converting field names to lowercase for {file_name}")
                temp_output = f"{full_geojson_file}.lowercase"
                try:
                    gdf = gpd.read_file(full_geojson_file)
                    rename_map = {
                        'NAME_EN': 'name_en', 'NAME_NL': 'name_nl', 'NAME_DE': 'name_de',
                        'NAME_FR': 'name_fr', 'NAME_ES': 'name_es', 'NAME_PT': 'name_pt',
                        'NAME_RU': 'name_ru', 'NAME_AR': 'name_ar',
                        'NAME_ZHT': 'name_zht', 'NAME_JA': 'name_ja', 'NAME_KO': 'name_ko',
                        'NAME_HI': 'name_hi', 'NAME_IT': 'name_it', 'NAME_PL': 'name_pl',
                        'NAME_SV': 'name_sv', 'NAME_TR': 'name_tr', 'NAME_EL': 'name_el',
                        'NAME_BN': 'name_bn', 'NAME_FA': 'name_fa', 'NAME_HE': 'name_he',
                        'NAME_UK': 'name_uk', 'NAME_UR': 'name_ur', 'NAME_VI': 'name_vi',
                        'NAME_HU': 'name_hu', 'NAME_ID': 'name_id',
                    }
                    gdf = gdf.rename(columns=rename_map)
                    gdf['name_zhs'] = None  # NAME_ZH does not exist in Natural Earth

                    def _minzoom(geom):
                        area = max(geom.area * 12321, 0.44)
                        return max(0, min(6, round(6 - (math.log10(area) + 0.5) * 0.75)))

                    gdf['minzoom'] = gdf.geometry.apply(_minzoom)

                    keep_cols = ['geometry'] + list(rename_map.values()) + ['name_zhs', 'minzoom']
                    keep_cols = [c for c in keep_cols if c in gdf.columns or c == 'geometry']
                    gdf[keep_cols].to_file(temp_output, driver='GeoJSON')
                    os.replace(temp_output, full_geojson_file)
                    print(f"  ✓ Converted field names to lowercase")
                except Exception as e:
                    print(f"  ✗ Error converting field names: {e}")
                    if os.path.exists(temp_output):
                        os.remove(temp_output)

        # Process Natural Earth roads for L0 (Z4-6)
        ne_roads_geojson = os.path.join(l0_temp_dir, "ne_10m_roads.geojson")
        if os.path.exists(ne_roads_geojson):
            print(f"Processing Natural Earth roads for L0")
            temp_output = f"{ne_roads_geojson}.filtered"
            try:
                gdf = gpd.read_file(ne_roads_geojson)
                keep_types = {'Major Highway', 'Expressway', 'Freeway', 'Ferry'}
                gdf = gdf[gdf['type'].isin(keep_types)].copy()
                gdf.geometry = gdf.geometry.simplify(0.01, preserve_topology=True)
                type_to_highway = {
                    'Expressway': 'motorway', 'Major Highway': 'motorway',
                    'Freeway': 'motorway', 'Ferry': 'ferry',
                }
                gdf['minzoom'] = 4
                gdf['highway'] = gdf['type'].map(type_to_highway).fillna('trunk')
                gdf['is_ferry'] = (gdf['type'] == 'Ferry').astype(int)
                gdf.to_file(temp_output, driver='GeoJSON')
                os.replace(temp_output, ne_roads_geojson)
                print(f"  ✓ Filtered and simplified Natural Earth roads")
            except Exception as e:
                print(f"  ✗ Error processing Natural Earth roads: {e}")
                if os.path.exists(temp_output):
                    os.remove(temp_output)

        # Unzip OSM land-polygons shapefile
        osm_land_zip = os.path.join(
            self.config.download_dir(), "osm-land", "land-polygons.zip")
        osm_land_dir = os.path.join(self.config.download_dir(), "osm-land")
        print(f"Unzipping {osm_land_zip}")
        unzip_file(osm_land_zip, osm_land_dir)
        osm_land_shp = os.path.join(osm_land_dir, "land-polygons-split-4326", "land_polygons.shp")
        ne_urban_path = os.path.join(temp_dir, "L0", "ne_10m_urban_areas.geojson")

        # STEP 3: Filter L1/L2 features by zoom level
        if not filter_L1_L2(
                temp_dir, min_lat, max_lat, min_lon, max_lon,
                self.config.tile_size(), gen_tile=self.gen_tile,
                with_service_roads=self.with_service_roads):
            return False

        # STEP 4: Convert filtered PBF to GeoJSON
        if not extract_L1_L2_features(
                temp_dir, min_lat, max_lat, min_lon, max_lon,
                self.config.tile_size(), osm_land_shp,
                ne_urban_path=ne_urban_path,
                gen_tile=self.gen_tile):
            return False

        # STEP 5: Generate MBTiles with tippecanoe
        build_mbtiles(temp_dir, out_dir, tippecanoe_tool)

        # STEP 6: Create tar archive
        tar_file = os.path.join(self.config.result_dir(), "tiles.tar")
        print(f"Creating tar file {tar_file}")
        archive_tiles(out_dir, tar_file)

        # STEP 7: Track in manifest (local file only)
        self.manifest.add_tiles(
                self.config.result_dir(), "tiles.tar",
                "tiles", "tiles.tar")
        self.manifest.save()

        return True

