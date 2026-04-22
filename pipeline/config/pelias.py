import os


class Pelias:
    def __init__(self,
                 data: dict,
                 config_path: str):
        self.config_file_name = data["config"]
        self.config_prod_file_name = data.get("config_prod", "pelias-prod.json")
        self.es_version = data["elastic"]["version"]
        self.es_api_version = data["elastic"]["api_version"]
        self.es_host = data["elastic"]["host"]
        self.es_port = data["elastic"]["port"]
        self.es_local_port = data.get("es_local_port", data["elastic"]["port"])
        self.es_host_prod = data.get("es_host_prod", "elasticsearch")
        self.es_port_prod = data.get("es_port_prod", 9200)
        self.config_path = config_path
        self.placeholder_url = data["placeholder_url"]
        self.snapshot_repo_path = data.get(
            "snapshot_repo_path", "/usr/share/elasticsearch/snapshots")
        self.snapshot_repo_name = data.get(
            "snapshot_repo_name", "pelias_repo")

    def config_template_file(self) -> str:
        return self.config_file_name

    def config_template_file_path(self) -> str:
        return os.path.join(self.config_path, self.config_file_name)

    def config_prod_template_file(self) -> str:
        return self.config_prod_file_name

    def config_prod_template_file_path(self) -> str:
        return os.path.join(self.config_path, self.config_prod_file_name)

    def elasticsearch_api_version(self) -> str:
        return self.es_api_version

    def elasticsearch_version(self) -> str:
        return self.es_version

    def elasticsearch_host(self) -> str:
        return self.es_host

    def elasticsearch_port(self) -> str:
        return self.es_port

    def elasticsearch_local_port(self) -> int:
        return self.es_local_port

    def elasticsearch_host_prod(self) -> str:
        return self.es_host_prod

    def elasticsearch_port_prod(self) -> int:
        return self.es_port_prod

    def snapshot_repository_path(self) -> str:
        return self.snapshot_repo_path

    def snapshot_repository_name(self) -> str:
        return self.snapshot_repo_name
