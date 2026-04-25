import os
import shutil
from .config import Config
from .manifest import Manifest


MANIFEST_ARCHIVES = [
    ("manifest-osm.yml",      ["osm.tar.bz2"]),
    ("manifest-border.yml",   ["border.tar.bz2"]),
    ("manifest-valhalla.yml", ["valhalla.tar.bz2"]),
    ("manifest-pelias.yml",   ["pelias-es-snapshot.tar.bz2", "pelias-data.tar.bz2"]),
    ("manifest-tiles.yml",    ["tiles.tar"]),
]


class Publish:
    def __init__(self, args):
        self.args = args
        self.config = Config(self.args.config)

    def run(self) -> bool:
        result_dir = str(self.config.result_dir())
        geodata_dir = self.config.geodata_output_dir()

        for manifest_filename, archive_filenames in MANIFEST_ARCHIVES:
            manifest_path = os.path.join(result_dir, manifest_filename)
            if not os.path.exists(manifest_path):
                print(f"Skipping {manifest_filename} (not found)")
                continue

            manifest = Manifest(path=result_dir, filename=manifest_filename)
            if not manifest.is_closed():
                print(f"Skipping {manifest_filename} (pipeline not completed)")
                continue

            tag = manifest.tag
            dest_dir = os.path.join(geodata_dir, tag)
            os.makedirs(dest_dir, exist_ok=True)

            for archive in archive_filenames:
                src = os.path.join(result_dir, archive)
                if not os.path.exists(src):
                    print(f"Warning: archive not found: {src}")
                    continue
                dst = os.path.join(dest_dir, archive)
                shutil.move(src, dst)
                print(f" > {src} -> {dst}")

        return True
