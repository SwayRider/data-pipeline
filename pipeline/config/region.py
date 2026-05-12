class Region:
    def __init__(self,
                 name: str,
                 data: dict,
                 osm_download_url: str,
                 srtm_download_url: str):
        self.name = name
        self.core_countries = []
        self.overlap_countries = []
        self.overlap_polygons = []
        self.core_wof = []
        self.overlap_wof = []
        self.core_openaddresses = []
        self.overlap_openaddresses = []
        self.srtm = []
        self._gtfs_feeds = []
        self.osm_download_url = osm_download_url
        self.srtm_download_url = srtm_download_url

        try:
            self._parse(data)
        except Exception as e:
            raise Exception(f"Error parsing region {name}: {e}")

    def osm_urls(
            self,
            include_core: bool = True,
            include_overlap: bool = True) -> list[tuple[str, str]]:
        url_base = self.osm_download_url
        urls_and_files = []

        if include_core:
            for item in self.core_countries:
                parts = item.split("/")
                if len(parts) == 2:
                    urls_and_files.append((
                        f"{url_base}{parts[0]}/{parts[1]}-latest.osm.pbf",
                        f"{item.replace('/', '_')}.osm.pbf"))
                else:
                    urls_and_files.append((
                        f"{url_base}{item}-latest.osm.pbf",
                        f"{item}.osm.pbf"))

        if include_overlap:
            for item in self.overlap_countries:
                parts = item.split("/")
                if len(parts) == 2:
                    urls_and_files.append((
                        f"{url_base}{parts[0]}/{parts[1]}-latest.osm.pbf",
                        f"{item.replace('/', '_')}.osm.pbf"))
                else:
                    urls_and_files.append((
                        f"{url_base}{item}-latest.osm.pbf",
                        f"{item}.osm.pbf"))

        return urls_and_files

    def srtm_urls(self) -> list[tuple[str, str, str, str]]:
        url_base = self.srtm_download_url
        all_tiles = set()
        urls_and_files = []
        for part in self.srtm:
            for _, arr in part.items():
                all_tiles.update(self._tile_list_for_box(*arr))

        for elem in all_tiles:
            tile, file, subdir = self._tile_file_and_subdir(*elem)
            url = f"{url_base}{subdir}/{tile}"
            gz_file = tile
            hgt_file = file
            urls_and_files.append((url, gz_file, hgt_file, subdir))
        return urls_and_files

    def core_source_files(self) -> list[str]:
        files = []
        for item in self.core_countries:
            files.append(item.replace("/", "_") + ".osm.pbf")
        return files

    def overlap_source_files(self) -> list[str]:
        files = []
        for item in self.overlap_countries:
            files.append(item.replace("/", "_") + ".osm.pbf")
        return files

    def overlap_polygon_files(self) -> list[str]:
        return self.overlap_polygons

    def openaddresses_files(self) -> list[str]:
        files = []
        for item in self.core_openaddresses:
            files.append(item)
        for item in self.overlap_openaddresses:
            files.append(item)
        return files

    def wof_country_codes(self) -> list[str]:
        codes = []
        for item in self.core_wof:
            codes.append(item)
        for item in self.overlap_wof:
            codes.append(item)
        return codes

    def gtfs_feeds(self) -> list[str]:
        return self._gtfs_feeds

    def bbox(self) -> tuple[float, float, float, float]:
        """Returns (min_lon, min_lat, max_lon, max_lat) derived from srtm extents."""
        min_lat = min(arr[0] for part in self.srtm for arr in part.values())
        max_lat = max(arr[1] for part in self.srtm for arr in part.values())
        min_lon = min(arr[2] for part in self.srtm for arr in part.values())
        max_lon = max(arr[3] for part in self.srtm for arr in part.values())
        return (min_lon, min_lat, max_lon, max_lat)

    def _tile_list_for_box(
            self,
            min_lat: float,
            max_lat: float,
            min_lon: float,
            max_lon: float) -> list[tuple[float, float]]:
        tiles = []
        for lat in range(min_lat, max_lat + 1):
            for lon in range(min_lon, max_lon + 1):
                tiles.append((lat, lon))
        return tiles

    def _tile_file_and_subdir(
            self,
            lat: float, lon: float) -> tuple[str, str, str]:
        ns = 'N' if lat >= 0 else 'S'
        ew = 'E' if lon >= 0 else 'W'
        tile = f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}.hgt.gz"
        file = tile[:-3]
        subdir = f"{ns}{abs(lat):02d}"
        return tile, file, subdir

    def _parse(self, config: dict):
        self.core_countries = config["core"]["osm"]
        self.overlap_countries = config["overlap"]["osm"]
        self.overlap_polygons = [f"overlap_{self.name}.poly"]
        self.core_wof = config["core"]["wof"]
        self.overlap_wof = config["overlap"]["wof"]
        self.core_openaddresses = config["core"]["openaddresses"]
        self.overlap_openaddresses = config["overlap"]["openaddresses"]
        self.srtm = config["srtm"]
        self._gtfs_feeds = config.get("gtfs_feeds", [])
