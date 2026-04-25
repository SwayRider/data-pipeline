class BorderRegion:
    def __init__(self, data: list):
        self.regions = data
        self.name = f"{data[0]}_{data[1]}"
        self.region_poly = f"br_{self.name}.poly"

    def osm_files(self) -> list[str]:
        return [f"{r}.osm.pbf" for r in self.regions]

    def osm_core_files(self) -> list[str]:
        return [f"{r}-core.osm.pbf" for r in self.regions]

    def core_region_border_files(self) -> list[str]:
        return [f"{r}-core.geojson" for r in self.regions]

    def extended_region_border_files(self) -> list[str]:
        return [f"{r}-extended.geojson" for r in self.regions]

    def poly_file(self) -> str:
        return self.region_poly

    def region_names(self) -> list[str]:
        return self.regions
