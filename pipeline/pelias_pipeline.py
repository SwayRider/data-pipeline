from .base_pipeline import BasePipeline
from .pelias_funcs import download_pelias_placeholder_data
from .pelias_funcs import cleanup_pelias_data, create_pelias_config
from .pelias_funcs import create_pelias_schema, import_pelias_wof
from .pelias_funcs import import_pelias_geonames, import_pelias_openaddresses
from .pelias_funcs import import_pelias_openstreetmap, import_pelias_polylines
from .pelias_funcs import download_overture_data, import_pelias_csv
from .pelias_funcs import download_gtfs_feeds, import_pelias_transit
from .pelias_funcs import archive_pelias_wof_data
from .pelias_funcs import wait_for_elasticsearch, create_snapshot_repository
from .pelias_funcs import snapshot_indices, create_alias, generate_prod_config
import os
import subprocess
import shutil


class PeliasDataPipeline(BasePipeline):
    def __init__(self, args):
        super().__init__(args, "manifest-pelias.yml")

    def _clean(self) -> None:
        temp_dir = self.config.temp_dir()
        result_dir = self.config.result_dir()
        for path in [
            os.path.join(temp_dir, "pelias-build"),
            os.path.join(temp_dir, "elasticsearch"),
            os.path.join(result_dir, "pelias"),
            os.path.join(result_dir, "es-snapshots"),
        ]:
            if os.path.exists(path):
                shutil.rmtree(path)
        for filename in ["manifest-pelias.yml",
                         "pelias-es-snapshot.tar.bz2",
                         "pelias-data.tar.bz2"]:
            manifest = os.path.join(result_dir, filename)
            if os.path.exists(manifest):
                os.remove(manifest)

    def _docker_compose_file(self) -> str:
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "docker", "docker-compose.pelias.yaml")

    def _start_local_es(self) -> bool:
        print("- Starting local Elasticsearch")
        pelias_config = self.config.pelias()
        es_data_dir = os.path.join(self.config.temp_dir(), "elasticsearch")
        es_snapshot_dir = os.path.join(self.config.result_dir(), "es-snapshots")
        os.makedirs(es_data_dir, exist_ok=True)
        os.makedirs(es_snapshot_dir, exist_ok=True)

        env = os.environ.copy()
        env["ES_DATA_DIR"] = es_data_dir
        env["ES_SNAPSHOT_DIR"] = es_snapshot_dir
        env["ES_PORT"] = str(pelias_config.elasticsearch_local_port())

        compose_file = self._docker_compose_file()
        try:
            subprocess.run(
                ["docker", "compose", "-f", compose_file, "up", "-d"],
                env=env, check=True)
        except subprocess.CalledProcessError as ex:
            print(f"  Failed to start ES container: {ex}")
            return False

        if not wait_for_elasticsearch(
                pelias_config.elasticsearch_host(),
                pelias_config.elasticsearch_local_port(),
                timeout=120):
            print("  Elasticsearch did not become ready")
            self._stop_local_es()
            return False

        return True

    def _stop_local_es(self) -> bool:
        print("- Stopping local Elasticsearch")
        pelias_config = self.config.pelias()
        es_data_dir = os.path.join(self.config.temp_dir(), "elasticsearch")
        es_snapshot_dir = os.path.join(self.config.result_dir(), "es-snapshots")

        env = os.environ.copy()
        env["ES_DATA_DIR"] = es_data_dir
        env["ES_SNAPSHOT_DIR"] = es_snapshot_dir
        env["ES_PORT"] = str(pelias_config.elasticsearch_local_port())

        compose_file = self._docker_compose_file()
        try:
            subprocess.run(
                ["docker", "compose", "-f", compose_file, "down", "-v"],
                env=env, check=True)
        except subprocess.CalledProcessError as ex:
            print(f"  Warning: failed to stop ES container: {ex}")
            return False
        return True

    def _snapshot_and_export(self) -> bool:
        print("- Snapshotting and exporting Elasticsearch data")
        pelias_config = self.config.pelias()
        host = pelias_config.elasticsearch_host()
        port = pelias_config.elasticsearch_local_port()
        repo_name = pelias_config.snapshot_repository_name()
        repo_path = pelias_config.snapshot_repository_path()

        if not create_snapshot_repository(host, port, repo_name, repo_path):
            print("  Failed to create snapshot repository")
            return False

        result_base_path = os.path.join(self.config.result_dir(), "pelias")
        indices_created = []

        for region in self.config.regions():
            region_name = region.name
            index_name = f"pelias_{region_name}-{self.manifest.tag}"
            alias_name = f"pelias_{region_name}"
            result_path = os.path.join(result_base_path, region_name)
            os.makedirs(result_path, exist_ok=True)

            if not create_alias(host, port, index_name, alias_name):
                print(f"  Failed to create alias for {region_name}")
                return False

            snapshot_name = f"pelias_{region_name}-{self.manifest.tag}"
            if not snapshot_indices(host, port, repo_name,
                                    snapshot_name, [index_name]):
                print(f"  Failed to snapshot {region_name}")
                return False

            if not generate_prod_config(
                    region_name, result_path, pelias_config):
                print(f"  Failed to generate prod config for {region_name}")
                return False

            indices_created.append(index_name)

        print(f"  Snapshot export complete ({len(indices_created)} indices)")
        return True

    def _patch_tools(self) -> bool:
        """Patch npm tools that use child_process.exec with unquoted paths."""
        import re
        schema_path = self.tools["pelias"]["schema"]
        create_index_js = os.path.join(
            schema_path, "scripts", "create_index.js")
        if not os.path.exists(create_index_js):
            return True

        with open(create_index_js, "r") as f:
            content = f.read()

        # Fix unquoted __dirname in child_process.execSync calls so tools work
        # when installed in directories with spaces. Node.js resolves symlinks
        # for __dirname, so the real path (with spaces) is always used.
        # Handles both unpatched and partially-patched states: wraps the full
        # .js path in double quotes (opening before ${__dirname}, closing after
        # the filename).
        patched = re.sub(
            r'execSync\(`node "?\$\{__dirname\}/(\S+?\.js)"?',
            r'execSync(`node "${__dirname}/\1"',
            content)

        if patched != content:
            print("  Patching schema tool for paths with spaces")
            with open(create_index_js, "w") as f:
                f.write(patched)

        return True

    def run(self) -> bool:
        if self.manifest.is_closed():
            print("Pipeline already completed")
            return True

        snapshot_only = getattr(self.args, "snapshot_only", False)

        if not self._start_local_es():
            print("Failed to start local Elasticsearch")
            return False

        try:
            if not snapshot_only:
                if not self._prepare_tools(["pelias"]):
                    print("Failed to prepare tools")
                    return False

                if not self._patch_tools():
                    print("Failed to patch tools")
                    return False

                required_files = [
                    os.path.join(self.config.result_dir(),
                                 "osm", region.name + ".osm.pbf")
                    for region in self.config.regions()
                ]
                if not self._check_prerequisites(required_files):
                    print("Prerequisites missing — run prepare-source-data first")
                    return False

                if not self._create_pelias_data():
                    print("Failed to create pelias data")
                    return False

            if not self._snapshot_and_export():
                print("Failed to snapshot and export")
                return False
        finally:
            self._stop_local_es()

        pelias_output_dir = os.path.join(self.config.result_dir(), "pelias")
        es_snapshot_dir = os.path.join(self.config.result_dir(), "es-snapshots")

        if not self._package_output(es_snapshot_dir,
                                    "pelias-es-snapshot.tar.bz2"):
            print("Failed to package ES snapshot")
            return False

        self.manifest.add_pelias_es_snapshot(
            self.config.result_dir(), "pelias-es-snapshot.tar.bz2")
        self.manifest.save()

        if not self._package_output(pelias_output_dir,
                                    "pelias-data.tar.bz2",
                                    exclude=["es-snapshots"]):
            print("Failed to package pelias data")
            return False

        print("Finishing up")
        self.manifest.mark_closed()
        self.manifest.save()
        print("Done")
        return True

    def _symlink_path(self, real_path: str, symlink_name: str) -> str:
        """Create a space-free symlink for npm tools that use child_process.exec."""
        symlink = f"/tmp/{symlink_name}"
        if os.path.islink(symlink):
            os.remove(symlink)
        elif os.path.exists(symlink):
            shutil.rmtree(symlink)
        os.symlink(real_path, symlink)
        return symlink

    def _create_pelias_data(self) -> bool:
        print("- Creating pelias data")

        # Create symlinks to avoid spaces in paths (npm tools use
        # child_process.exec with unquoted __dirname)
        build_path_real = os.path.join(
                self.config.temp_dir(), "pelias-build")
        os.makedirs(build_path_real, exist_ok=True)
        build_path = self._symlink_path(build_path_real, "pelias-build")

        result_base_path = os.path.join(
                self.config.result_dir(), "pelias")

        schema_tools_path = self._symlink_path(
                self.tools["pelias"]["schema"], "pelias-tools-schema")
        wof_tools_path = self._symlink_path(
                self.tools["pelias"]["whosonfirst"], "pelias-tools-wof")
        geonames_tools_path = self._symlink_path(
                self.tools["pelias"]["geonames"], "pelias-tools-geonames")
        openaddresses_tools_path = self._symlink_path(
                self.tools["pelias"]["openaddresses"], "pelias-tools-oa")
        openstreetmap_tools_path = self._symlink_path(
                self.tools["pelias"]["openstreetmap"], "pelias-tools-osm")
        polylines_tools_path = self._symlink_path(
                self.tools["pelias"]["polylines"], "pelias-tools-polylines")
        csv_tools_path = self._symlink_path(
                self.tools["pelias"]["csv"], "pelias-tools-csv")
        transit_tools_path = self._symlink_path(
                self.tools["pelias"]["transit"], "pelias-tools-transit")

        pelias_config = self.config.pelias()

        os.makedirs(build_path, exist_ok=True)
        os.makedirs(result_base_path, exist_ok=True)

        print("  Downloading placeholder data")
        if self.manifest.pelias_placeholder_data_exists():
            print("    Skipping, already exists")
        else:
            res, placeholder_file_name = download_pelias_placeholder_data(
                    result_base_path, pelias_config.placeholder_url)
            if not res:
                return False

            self.manifest.add_pelias_placeholder_data(
                    result_base_path,
                    placeholder_file_name,
                    "pelias/placeholder",
                    placeholder_file_name)
            self.manifest.save()

        for region in self.config.regions():
            print(f"  - {region.name}")

            if self.manifest.pelias_whosonfirst_data_exists(region.name):
                print("    Skipping, already exists")
                continue

            result_path = os.path.join(result_base_path, region.name)
            os.makedirs(result_path, exist_ok=True)

            print("    Cleaning old data")
            res = cleanup_pelias_data(build_path)
            if not res:
                return False

            data_path = os.path.join(build_path, "data")
            overture_data_path = os.path.join(
                    data_path, "overture", region.name)
            gtfs_data_path = os.path.join(
                    data_path, "gtfs", region.name)

            print("    Generating pelias.json config file")
            res = create_pelias_config(
                    region.name,
                    self.manifest.tag,
                    build_path,
                    self.config.result_dir(),
                    result_path,
                    pelias_config,
                    region.openaddresses_files(),
                    region.wof_country_codes(),
                    overture_data_path,
                    gtfs_data_path)
            if not res:
                return False

            self.manifest.add_pelias_config(
                    region.name,
                    result_path,
                    "pelias.json",
                    f"pelias/config/{region.name}",
                    "pelias.json")
            self.manifest.save()

            print("    Creating pelias schema")
            res, index_name = create_pelias_schema(
                    region.name, self.manifest.tag,
                    schema_tools_path, build_path)
            if not res:
                return False

            self.manifest.add_pelias_index(region.name, index_name)
            self.manifest.save()

            print("    Importing WOF data")
            res = import_pelias_wof(wof_tools_path, build_path)
            if not res:
                return False

            print("    Importing geonames data")
            res = import_pelias_geonames(
                    region.name, geonames_tools_path, build_path)
            if not res:
                return False

            print("    Importing openaddresses data")
            res = import_pelias_openaddresses(
                    openaddresses_tools_path, build_path)
            if not res:
                return False

            print("    Importing openstreetmap data")
            res = import_pelias_openstreetmap(
                    openstreetmap_tools_path, build_path)
            if not res:
                return False

            print("    Importing polylines (streets)")
            res = import_pelias_polylines(polylines_tools_path, build_path)
            if not res:
                return False

            print("    Downloading Overture data (places + addresses)")
            os.makedirs(overture_data_path, exist_ok=True)
            res = download_overture_data(region.bbox(), overture_data_path)
            if not res:
                print("    Warning: Overture download failed, continuing without it")
            else:
                print("    Importing Overture data (CSV)")
                res = import_pelias_csv(csv_tools_path, build_path)
                if not res:
                    return False

            feeds = region.gtfs_feeds()
            if feeds:
                print("    Downloading GTFS feeds")
                os.makedirs(gtfs_data_path, exist_ok=True)
                res = download_gtfs_feeds(feeds, gtfs_data_path)
                if not res:
                    return False
                print("    Importing GTFS transit stops")
                res = import_pelias_transit(transit_tools_path, build_path)
                if not res:
                    return False
            else:
                print("    Skipping GTFS (no feeds configured)")

            print("    Archiving WOF data")
            res, file_path = archive_pelias_wof_data(
                    region.name, build_path, result_path)
            if not res:
                return False

            file_name = os.path.basename(file_path)
            local_path = os.path.dirname(file_path)

            self.manifest.add_pelias_whosonfirst_data(
                region.name,
                local_path,
                file_name,
                f"pelias/wof/{region.name}",
                file_name)
            self.manifest.save()

        return True
