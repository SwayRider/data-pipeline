import os
import shutil
from .config import Config
from .manifest import Manifest


class Publish:
    def __init__(self, args):
        self.args = args
        self.config = Config(self.args.config)
        manifest_file = getattr(self.args, 'manifest', 'manifest.yml')
        self.manifest = Manifest(
            path=str(self.config.result_dir()),
            filename=manifest_file)
        self.manifest.load(str(self.config.result_dir()))

    def run(self) -> bool:
        output_dir = self.config.geodata_output_dir()
        tag = self.manifest.tag

        # Copy contours
        for region_name, region_data in self.manifest.regions.items():
            if "contour" not in region_data:
                continue
            for kind in ("core", "extended"):
                entry = region_data["contour"][kind]
                src = entry["local-file"]
                dst = os.path.join(output_dir, entry["remote-file"])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                print(f" > {src} -> {dst}")

        # Copy border crossings
        for name, data in self.manifest.shared.get("border-crossings", {}).items():
            src = data["local-file"]
            dst = os.path.join(output_dir, data["remote-file"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            print(f" > {src} -> {dst}")

        # Write manifest.yml into the tag directory
        tag_dir = os.path.join(output_dir, tag)
        os.makedirs(tag_dir, exist_ok=True)
        manifest_dst = os.path.join(tag_dir, "manifest.yml")
        shutil.copy2(
            os.path.join(self.manifest.path, self.manifest.filename),
            manifest_dst)
        print(f" > manifest -> {manifest_dst}")

        return True
