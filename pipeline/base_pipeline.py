from .manifest import Manifest
from .config import Config
from .dirs import init_dirs, cleanup_dirs
import os
import subprocess


class BasePipeline:
    def __init__(self, args, manifest_filename: str):
        self.args = args
        self.config = Config(self.args.config)
        self.manifest = None
        self.tools = {}
        self._manifest_filename = manifest_filename

    def initialize(self, readonly: bool = False) -> bool:
        print("- Initializing pipeline")
        tag = None

        if not readonly:
            if self.args.clean:
                self._clean()
            elif getattr(self.args, 'clean_all', False):
                cleanup_dirs(self.config)
            init_dirs(self.config)
            tag = self.args.tag if not self.args.tag == "" else None

        self.manifest = Manifest(
            path=str(self.config.result_dir()),
            tag=tag,
            path_prefix="",
            filename=self._manifest_filename)

        if not readonly:
            self.manifest.save()

        return True

    def _prepare_tools(self, tool_categories: list) -> bool:
        print("- Installing tools")
        for category in tool_categories:
            repo_list = getattr(self.config, f"{category}_repos")()
            self.tools[category] = {}
            for repo in repo_list:
                source_path = repo.pull(self.config.tools_dir())
                self.tools[category][repo.name] = source_path
                install_ok = repo.install(self.config.tools_dir())
                if not install_ok:
                    print(f"  Error: failed to install {category}")
                    return False

        return True

    def _check_prerequisites(self, required_files: list) -> bool:
        missing = [f for f in required_files if not os.path.exists(f)]
        if missing:
            for f in missing:
                print(f"  Error: required file not found: {f}")
            return False
        return True

    def _package_output(self, source_dir: str, tar_filename: str,
                        exclude: list[str] = None) -> bool:
        output_tar = os.path.join(self.config.result_dir(), tar_filename)
        print(f"  Creating archive {output_tar}")
        cmd = ["tar", "-cjf", output_tar, "-C", source_dir]
        if exclude:
            for pattern in exclude:
                cmd.append(f"--exclude={pattern}")
        cmd.append(".")
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            print(f"  Error: tar failed with exit code {result.returncode}")
            return False
        return True

    def _clean(self) -> None:
        cleanup_dirs(self.config)

    def run(self) -> bool:
        raise NotImplementedError
