import yaml
from pathlib import Path
from .github_repo import GithubRepo
from .region import Region
from .border_region import BorderRegion
from .pelias import Pelias


class Config:
    def __init__(self, path: str = "./config/config.yml"):
        self.path = path
        with open(path, "r") as f:
            self.dct = yaml.safe_load(f)

    def max_worders(self) -> int:
        return self.dct["max_workers"]

    def config_dir(self) -> Path:
        return Path(self.dct["source_paths"]["config"]).resolve()

    def gis_export_dir(self) -> Path:
        return Path(self.dct["source_paths"]["gis_export"]).resolve()

    def border_polygons_dir(self) -> Path:
        return self.gis_export_dir() / "borders"

    def overlap_polygons_dir(self) -> Path:
        return self.gis_export_dir() / "overlap"

    def download_dir(self) -> Path:
        return Path(self.dct["build_paths"]["download_dir"]).resolve()

    def temp_dir(self) -> Path:
        return Path(self.dct["build_paths"]["temp_dir"]).resolve()

    def tools_dir(self) -> Path:
        return Path(self.dct["build_paths"]["tools_dir"]).resolve()

    def result_dir(self) -> Path:
        return Path(self.dct["build_paths"]["result_dir"]).resolve()

    def valhalla_repos(self) -> list[GithubRepo]:
        return self._repos("valhalla")

    def pelias_repos(self) -> list[GithubRepo]:
        return self._repos("pelias")

    def tippecanoe_repos(self) -> list[GithubRepo]:
        return self._repos("tippecanoe")

    def osgeo_repos(self) -> list[GithubRepo]:
        return self._repos("osgeo")

    def region_size(self) -> tuple[int, int, int, int]:
        return (
                self.dct["region_size"]["min_lon"],
                self.dct["region_size"]["min_lat"],
                self.dct["region_size"]["max_lon"],
                self.dct["region_size"]["max_lat"])

    def tile_size(self) -> int:
        return self.dct["tile_size"]

    def natural_earth_files(self) -> list[str]:
        return self.dct["natural_earth"]

    def tile_regions(self) -> list[str]:
        return self.dct["tile_regions"]

    def regions(self) -> list[Region]:
        region_list = []
        for region in self.dct["regions"]:
            for region_name, region_data in region.items():
                region_list.append(Region(
                    region_name, region_data,
                    self.osm_download_url(), self.srtm_download_url()
                ))
        return region_list

    def border_regions(self) -> list[BorderRegion]:
        border_crossings_list = []
        for bc in self.dct["border-regions"]:
            border_crossings_list.append(BorderRegion(bc))
        return border_crossings_list

    def osm_download_url(self) -> str:
        return self.dct["download_urls"]["osm"]

    def srtm_download_url(self) -> str:
        return self.dct["download_urls"]["srtm"]

    def natural_earth_download_url(self) -> str:
        return self.dct["download_urls"]["natural-earth"]

    def osm_land_polygons_url(self) -> str:
        return self.dct["download_urls"]["osm-land-polygons"]

    def pelias(self) -> Pelias:
        return Pelias(self.dct["pelias"], self.config_dir())

    def geodata_output_dir(self) -> str:
        return self.dct["output"]["geodata_dir"]

    def _repos(self, kind: str) -> list[GithubRepo]:
        repo_list = []
        base_url = self.dct[kind]["repo_base"]
        for repo in self.dct[kind]["repos"]:
            for repo_name_and_tag, repo_data in repo.items():
                repoObj = GithubRepo(base_url, repo_name_and_tag)

                if repo_data.get("git-submodule-update", False):
                    repoObj.set_init_submodules()
                if repo_data.get("cmake-build", False):
                    repoObj.set_cmake_build(repo_data.get("cmake-opts", []))
                if repo_data.get("cmake-compile", False):
                    repoObj.set_cmake_compile()
                if repo_data.get("cmake-install", False):
                    repoObj.set_cmake_install()
                if repo_data.get("make-build", False):
                    repoObj.set_make_build(repo_data.get("make-opts", []))
                if repo_data.get("make-install", False):
                    repoObj.set_make_install()
                if repo_data.get("npm-install", False):
                    repoObj.set_npm_install()
                if repo_data.get("npm-extra-pkgs", []):
                    repoObj.set_npm_extra_pkgs(repo_data.get("npm-extra-pkgs", []))

                repo_list.append(repoObj)

        return repo_list
