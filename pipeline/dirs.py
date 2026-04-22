from .config import Config
import shutil
import os


def cleanup_dirs(config: Config) -> None:
    shutil.rmtree(str(config.download_dir()))
    shutil.rmtree(str(config.temp_dir()))
    shutil.rmtree(str(config.tools_dir()))
    shutil.rmtree(str(config.result_dir()))


def init_dirs(config: Config) -> None:
    os.makedirs(str(config.download_dir()), exist_ok=True)
    os.makedirs(str(config.temp_dir()), exist_ok=True)
    os.makedirs(str(config.tools_dir()), exist_ok=True)
    os.makedirs(str(config.result_dir()), exist_ok=True)
