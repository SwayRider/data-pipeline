import datetime
import hashlib
import os
import yaml


now = datetime.datetime.now


class Manifest:
    def __init__(self, path: str = None, tag: str = None, path_prefix: str = "data", filename: str = "manifest.yml"):
        self.path = path
        self.path_prefix = path_prefix  # Configurable prefix (default "data")
        self.filename = filename  # Configurable filename (default "manifest.yml")
        self.started_at = now().strftime("%Y-%m-%dT%H:%M:%S.%f%z")
        self.completed_at = None
        self.tag = tag if tag else now().strftime("%Y-%m-%d")
        self.regions = {}
        self.shared = {}
        if path is not None:
            self.load(path)
        if tag and tag != self.tag:
            raise Exception(f"Tag mismatch: {tag} != {self.tag}")

    def load(self, path: str):
        file_name = os.path.join(path, self.filename)
        if not os.path.exists(file_name):
            return
        with open(file_name, "r") as f:
            dct = yaml.safe_load(f)
            self.path = dct["path"]
            self.started_at = dct["started-at"]

            if "completed-at" in dct:
                self.completed_at = dct["completed-at"]
            else:
                self.completed_at = None

            self.tag = dct["tag"]
            self.regions = dct["regions"] if "regions" in dct else {}
            self.shared = dct["shared"] if "shared" in dct else {}

    def save(self):
        if not self.path:
            raise Exception("Manifest path not set")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        file_name = os.path.join(self.path, self.filename)
        dct = self._to_dict()
        with open(file_name, "w") as f:
            yaml.dump(dct, f)

    def add_pbf(
            self,
            region: str,
            local_path: str,
            local_filename: str,
            remote_path: str,
            remote_filename: str):
        self._add_region(region)
        self.regions[region]["osm"] = {
            "local-file": os.path.join(local_path, local_filename),
            "remote-file": f"{remote_path}/{remote_filename}",
            "hash-type": "md5",
            "hash": self._md5_hash(os.path.join(local_path, local_filename))
        }

    def pbf_exists(self, region: str) -> bool:
        if region not in self.regions:
            return False
        return "osm" in self.regions[region]

    def add_tiles(
            self,
            local_tiles_path: str,
            local_tiles_filenae: str,
            remote_tiles_path: str,
            remote_tiles_filename: str):
        self.shared["tiles"] = {
            "local-file": os.path.join(local_tiles_path, local_tiles_filenae),
            "remote-file": f"{remote_tiles_path}/{remote_tiles_filename}",
            "hash-type": "md5",
            "hash": self._md5_hash(os.path.join(local_tiles_path, local_tiles_filenae))
        }

    def tiles_exists(self, name: str) -> bool:
        if "tiles" not in self.shared:
            return False
        return name in self.shared["tiles"]

    def add_contours(
            self,
            region: str,
            core_local_path: str,
            core_local_filename: str,
            ext_local_path: str,
            ext_local_filename: str,
            core_remote_path: str,
            core_remote_filename: str,
            ext_remote_path: str,
            ext_remote_filename: str):
        self._add_region(region)
        self.regions[region]["contour"] = {
            "core": {
                "local-file": os.path.join(
                    core_local_path, core_local_filename),
                "remote-file":
                    f"{core_remote_path}/{core_remote_filename}",
                "hash-type": "md5",
                "hash": self._md5_hash(os.path.join(
                    core_local_path, core_local_filename))
            },
            "extended": {
                "local-file": os.path.join(
                    ext_local_path, ext_local_filename),
                "remote-file":
                    f"{ext_remote_path}/{ext_remote_filename}",
                "hash-type": "md5",
                "hash": self._md5_hash(os.path.join(
                    ext_local_path, ext_local_filename))
            }
        }

    def contours_exists(self, region: str) -> bool:
        if region not in self.regions:
            return False
        return "contour" in self.regions[region]

    def add_border_crossings(
            self,
            name: str,
            local_path: str,
            local_filename: str,
            remote_path: str,
            remote_filename: str):
        if "border-crossings" not in self.shared:
            self.shared["border-crossings"] = {}
        self.shared["border-crossings"][name] = {
            "local-file": os.path.join(local_path, local_filename),
            "remote-file": f"{remote_path}/{remote_filename}",
            "hash-type": "md5",
            "hash": self._md5_hash(os.path.join(local_path, local_filename))
        }

    def border_crossings_exists(self, name: str) -> bool:
        if "border-crossings" not in self.shared:
            return False
        return name in self.shared["border-crossings"]

    def add_valhalla_data(
            self,
            region: str,
            local_path: str,
            local_admin_filename: str,
            local_tz_filename: str,
            local_tiles_filename: str,
            remote_path: str,
            remote_admin_filename: str,
            remote_tz_filename: str,
            remote_tiles_filename: str):

        local_admin_file = os.path.join(local_path, local_admin_filename)
        local_tz_file = os.path.join(local_path, local_tz_filename)
        local_tiles_file = os.path.join(local_path, local_tiles_filename)

        remote_admin_file = f"{remote_path}/{remote_admin_filename}"
        remote_tz_file = f"{remote_path}/{remote_tz_filename}"
        remote_tiles_file = f"{remote_path}/{remote_tiles_filename}"

        self._add_region(region)
        self.regions[region]["valhalla"] = {
            "admin": {
                "local-file": local_admin_file,
                "remote-file": remote_admin_file,
                "hash-type": "md5",
                "hash": self._md5_hash(local_admin_file)
            },
            "timezones": {
                "local-file": local_tz_file,
                "remote-file": remote_tz_file,
                "hash-type": "md5",
                "hash": self._md5_hash(local_tz_file)
            },
            "tiles": {
                "local-file": local_tiles_file,
                "remote-file": remote_tiles_file,
                "hash-type": "md5",
                "hash": self._md5_hash(local_tiles_file)
            }
        }

    def valhalla_data_exists(self, region: str) -> bool:
        if region not in self.regions:
            return False
        return "valhalla" in self.regions[region]

    def add_pelias_index(
            self,
            region: str,
            pelias_index: str):
        self._add_region(region)
        if "pelias" not in self.regions[region]:
            self.regions[region]["pelias"] = {}
        self.regions[region]["pelias"]["index"] = pelias_index

    def pelias_index_exists(self, region: str) -> bool:
        if region not in self.regions:
            return False
        return "index" in self.regions[region]["pelias"]

    def add_pelias_placeholder_data(
            self,
            local_path: str,
            local_filename: str,
            remote_path: str,
            remote_filename: str):
        self.shared["pelias-placeholder"] = {
            "local-file": os.path.join(local_path, local_filename),
            "remote-file": f"{remote_path}/{remote_filename}",
            "hash-type": "md5",
            "hash": self._md5_hash(os.path.join(local_path, local_filename))
        }

    def pelias_placeholder_data_exists(self) -> bool:
        return "pelias-placeholder" in self.shared

    def add_pelias_config(
            self,
            region: str,
            local_path: str,
            local_filename: str,
            remote_path: str,
            remote_filename: str):
        self._add_region(region)
        if "pelias" not in self.regions[region]:
            self.regions[region]["pelias"] = {}
        self.regions[region]["pelias"]["config"] = {
            "local-file": os.path.join(local_path, local_filename),
            "remote-file": f"{remote_path}/{remote_filename}",
            "hash-type": "md5",
            "hash": self._md5_hash(os.path.join(local_path, local_filename))
        }

    def pelias_config_exists(self, region: str) -> bool:
        if region not in self.regions:
            return False
        return "config" in self.regions[region]["pelias"]

    def add_pelias_whosonfirst_data(
            self,
            region: str,
            local_path: str,
            local_filename: str,
            remote_path: str,
            remote_filename: str):
        self._add_region(region)
        if "pelias" not in self.regions[region]:
            self.regions[region]["pelias"] = {}
        self.regions[region]["pelias"]["whosonfirst"] = {
            "local-file": os.path.join(local_path, local_filename),
            "remote-file": f"{remote_path}/{remote_filename}",
            "hash-type": "md5",
            "hash": self._md5_hash(os.path.join(local_path, local_filename))
        }

    def pelias_whosonfirst_data_exists(self, region: str) -> bool:
        if region not in self.regions:
            return False
        if "pelias" not in self.regions[region]:
            return False
        return "whosonfirst" in self.regions[region]["pelias"]

    def add_pelias_es_snapshot(
            self,
            local_path: str,
            local_filename: str):
        self.shared["pelias-es-snapshot"] = {
            "local-file": os.path.join(local_path, local_filename),
            "hash-type": "md5",
            "hash": self._md5_hash(os.path.join(local_path, local_filename))
        }

    def pelias_es_snapshot_exists(self) -> bool:
        return "pelias-es-snapshot" in self.shared

    def mark_closed(self):
        self.completed_at = now().strftime("%Y-%m-%dT%H:%M:%S.%f%z")

    def is_closed(self) -> bool:
        return self.completed_at is not None

    def _to_dict(self) -> dict:
        dct = {
            "path": self.path,
            "started-at": self.started_at,
            "tag": self.tag,
            "regions": self.regions,
            "shared": self.shared
        }
        if self.completed_at:
            dct["completed-at"] = self.completed_at
        return dct

    def _add_region(self, region: str):
        if region not in self.regions:
            self.regions[region] = {}

    def _md5_hash(self, file_path: str):
        md5 = hashlib.md5()
        with open(os.path.join(self.path, file_path), "rb") as f:
            for chunk in iter(lambda: f.read(128 * md5.block_size), b""):
                md5.update(chunk)
        return md5.hexdigest()

    def regions_str_list(self) -> list[str]:
        return list(self.regions.keys())
