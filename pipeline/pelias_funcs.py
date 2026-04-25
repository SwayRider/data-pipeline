import os
import json
import subprocess
import shutil
import time
import urllib.request
import urllib.error
from .config import Pelias
from string import Template


def index_name(region_name: str, tag: str) -> str:
    return f"pelias_{region_name}-{tag}"


def download_pelias_placeholder_data(
        result_path: str,
        placeholder_url: str) -> tuple[bool, str]:
    file_name = "store.sqlite3.gz"
    out_file = os.path.join(result_path, file_name)
    if os.path.exists(out_file):
        print("File '{out_file}' already exists. Skipping download.")
        return (True, file_name)

    cmd = f"curl -o {file_name} {placeholder_url}"

    try:
        subprocess.run(cmd, cwd=result_path, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return (False, None)

    return (True, file_name)


def cleanup_pelias_data(build_path: str) -> bool:
    config_file_path = os.path.join(build_path, "pelias.json")
    data_path = os.path.join(build_path, "data")

    try:
        subprocess.run(f"rm -f \"{config_file_path}\"", shell=True, check=True)
        subprocess.run(f"rm -rf \"{data_path}\"", shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False
    return True


def create_pelias_config(
        region_name: str,
        tag: str,
        build_path: str,
        result_base_path: str,
        result_path: str,
        pelias_config: Pelias,
        open_addresses_files: list[str],
        wof_country_codes: list[str],
        overture_data_path: str = None,
        gtfs_data_path: str = None,
        polylines_data_path: str = None) -> bool:
    config_file_path = os.path.join(build_path, "pelias.json")
    prod_config_file_path = os.path.join(result_path, "pelias.json")
    data_path = os.path.join(build_path, "data")

    wof_data_path = os.path.join(data_path,
                                 "wof", region_name)
    geonames_data_path = os.path.join(data_path,
                                      "geonames", region_name)
    openaddresses_data_path = os.path.join(data_path,
                                           "openaddresses", region_name)
    openstreetmap_data_path = os.path.join(result_base_path,
                                           "osm")

    os.makedirs(wof_data_path, exist_ok=True)
    os.makedirs(geonames_data_path, exist_ok=True)
    os.makedirs(openaddresses_data_path, exist_ok=True)

    if overture_data_path is None:
        overture_data_path = os.path.join(data_path, "overture", region_name)
    os.makedirs(overture_data_path, exist_ok=True)

    if gtfs_data_path is None:
        gtfs_data_path = os.path.join(data_path, "gtfs", region_name)
    os.makedirs(gtfs_data_path, exist_ok=True)

    if polylines_data_path is None:
        polylines_data_path = os.path.join(result_base_path, "valhalla", region_name)

    template_data = {
            "es_api_version": pelias_config.elasticsearch_api_version(),
            "es_host": pelias_config.elasticsearch_host(),
            "es_port": pelias_config.elasticsearch_port(),
            "region_name": region_name,
            "tag": tag,
            "geonames_data_path": geonames_data_path,
            "openaddresses_data_path": openaddresses_data_path,
            "openaddresses_files": json.dumps(open_addresses_files),
            "openstreetmap_data_path": openstreetmap_data_path,
            "openstreetmap_file_name": f"{region_name}.osm.pbf",
            "polylines_data_path": polylines_data_path,
            "wof_data_path": wof_data_path,
            "wof_country_codes": json.dumps(wof_country_codes),
            "overture_data_path": overture_data_path,
            "gtfs_data_path": gtfs_data_path,
            "placeholder_url": "http://pelias-placeholder:3000",
            "libpostal_url": "http://pelias-libpostal:4400",
            "pip_url": f"http://pelias-{region_name}-pip:3102"
    }

    template_prod_data = {}
    template_prod_data.update(template_data)
    template_prod_data["es_host"] = "elasticsearch"
    template_prod_data["es_port"] = 9200
    template_prod_data["wof_data_path"] = "/data/whosonfirst"

    if os.path.exists(config_file_path):
        print(f"File {config_file_path} already exists. Skipping.")
        return False

    with open(pelias_config.config_template_file_path(), "r") as f:
        template = Template(f.read())
    json_string = template.substitute(**template_data)
    json_prod_string = template.substitute(**template_prod_data)

    with open(config_file_path, "w") as f:
        f.write(json_string)

    with open(prod_config_file_path, "w") as f:
        f.write(json_prod_string)

    return True


def create_pelias_schema(
        region_name: str,
        tag: str,
        schema_tools_path: str,
        build_path: str) -> tuple[bool, str]:
    config_file_path = os.path.join(build_path, "pelias.json")

    cmd0 = ["npm", "install", "pelias-config"]
    cmd1 = ["./bin/create_index"]

    env = os.environ.copy()
    env["PELIAS_CONFIG"] = config_file_path

    try:
        subprocess.run(cmd0, cwd=schema_tools_path, check=True)
        subprocess.run(cmd1, cwd=schema_tools_path, env=env, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return (False, None)

    # TODO --> this is now hardcoded to the value generated in the config
    #          we need to make this dynamic
    return (True, f"pelias_{region_name}-{tag}")


def import_pelias_wof(
        wof_tools_path: str,
        build_path: str) -> bool:
    config_file_path = os.path.join(build_path, "pelias.json")

    cmd0 = ["npm", "install", "pelias-config"]
    cmd1 = ["./bin/download"]
    cmd2 = ["./bin/start"]

    env = os.environ.copy()
    env["PELIAS_CONFIG"] = config_file_path

    try:
        subprocess.run(cmd0, cwd=wof_tools_path, check=True)
        subprocess.run(cmd1, cwd=wof_tools_path, env=env, check=True)
        subprocess.run(cmd2, cwd=wof_tools_path, env=env, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    return True


def import_pelias_geonames(
        region_name: str,
        geonames_tools_path: str,
        build_path: str) -> bool:
    config_file_path = os.path.join(build_path, "pelias.json")

    zip_download_path = os.path.join(build_path,
                                     "data", "geonames", region_name)
    zip_download_file = os.path.join(zip_download_path, "allCountries.zip")
    zip_extract_path = os.path.join(build_path, "geonames-zip")

    os.makedirs(zip_extract_path, exist_ok=True)

    # We unizip and rezip becuase the original zip can not always be read
    # correctly by pelias

    cmd0 = ["npm", "install", "pelias-config"]
    cmd1 = ["./bin/download"]
    cmd2 = f"unzip -o \"{zip_download_file}\""
    cmd3 = f"rm -f \"{zip_download_file}\""
    cmd4 = f"zip \"{zip_download_file}\" -r allCountries.txt"
    cmd5 = ["./bin/start"]

    env = os.environ.copy()
    env["PELIAS_CONFIG"] = config_file_path

    try:
        subprocess.run(cmd0, cwd=geonames_tools_path, check=True)
        subprocess.run(cmd1, cwd=geonames_tools_path, env=env, check=True)
        subprocess.run(cmd2, cwd=zip_extract_path, shell=True, check=True)
        subprocess.run(cmd3, cwd=zip_extract_path, shell=True, check=True)
        subprocess.run(cmd4, cwd=zip_extract_path, shell=True, check=True)
        subprocess.run(cmd5, cwd=geonames_tools_path, env=env, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    shutil.rmtree(zip_extract_path, ignore_errors=True)
    return True


def import_pelias_openaddresses(
        openaddresses_tools_path: str,
        build_path: str) -> bool:
    config_file_path = os.path.join(build_path, "pelias.json")

    cmd0 = ["npm", "install", "pelias-config"]
    cmd1 = ["./bin/download"]
    cmd2 = ["./bin/start"]

    env = os.environ.copy()
    env["PELIAS_CONFIG"] = config_file_path

    try:
        subprocess.run(cmd0, cwd=openaddresses_tools_path, check=True)
        subprocess.run(cmd1, cwd=openaddresses_tools_path, env=env, check=True)
        subprocess.run(cmd2, cwd=openaddresses_tools_path, env=env, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    return True


def import_pelias_openstreetmap(
        openstreetmap_tools_path: str,
        build_path: str) -> bool:
    config_file_path = os.path.join(build_path, "pelias.json")

    cmd0 = ["npm", "install", "pelias-config"]
    cmd1 = ["./bin/start"]

    env = os.environ.copy()
    env["PELIAS_CONFIG"] = config_file_path

    try:
        subprocess.run(cmd0, cwd=openstreetmap_tools_path, check=True)
        subprocess.run(cmd1, cwd=openstreetmap_tools_path, env=env, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    return True


def import_pelias_polylines(
        polylines_tools_path: str,
        build_path: str) -> bool:
    config_file_path = os.path.join(build_path, "pelias.json")

    cmd0 = ["npm", "install", "pelias-config"]
    cmd1 = ["./bin/start"]

    env = os.environ.copy()
    env["PELIAS_CONFIG"] = config_file_path

    try:
        subprocess.run(cmd0, cwd=polylines_tools_path, check=True)
        subprocess.run(cmd1, cwd=polylines_tools_path, env=env, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    return True


def _download_overture_theme(
        bbox: tuple[float, float, float, float],
        theme: str,
        geojson_file: str) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    cmd = [
        "overturemaps", "download",
        "--bbox", f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "-f", "geojson",
        "--type", theme,
        "-o", geojson_file
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False
    return True


def _convert_overture_places_to_csv(geojson_file: str, csv_file: str) -> None:
    import csv as csv_mod
    fieldnames = [
        "id", "name", "lat", "lon", "layer", "source",
        "category", "housenumber", "street", "postcode", "city", "country"
    ]
    with open(geojson_file, "r") as fin, \
         open(csv_file, "w", newline="") as fout:
        writer = csv_mod.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                feature = json.loads(line)
            except json.JSONDecodeError:
                continue
            props = feature.get("properties", {})
            coords = feature.get("geometry", {}).get("coordinates", [None, None])
            addresses = props.get("addresses", [{}])
            addr = addresses[0] if addresses else {}
            writer.writerow({
                "id": props.get("id", ""),
                "name": (props.get("names") or {}).get("primary", ""),
                "lat": coords[1],
                "lon": coords[0],
                "layer": "venue",
                "source": "overture",
                "category": (props.get("categories") or {}).get("primary", ""),
                "housenumber": addr.get("freeform", ""),
                "street": addr.get("street", ""),
                "postcode": addr.get("postcode", ""),
                "city": addr.get("locality", ""),
                "country": addr.get("country", ""),
            })


def _convert_overture_addresses_to_csv(geojson_file: str, csv_file: str) -> None:
    import csv as csv_mod
    fieldnames = [
        "id", "lat", "lon", "layer", "source",
        "housenumber", "street", "postcode", "city", "country"
    ]
    with open(geojson_file, "r") as fin, \
         open(csv_file, "w", newline="") as fout:
        writer = csv_mod.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                feature = json.loads(line)
            except json.JSONDecodeError:
                continue
            props = feature.get("properties", {})
            if not props.get("street"):
                continue
            coords = feature.get("geometry", {}).get("coordinates", [None, None])
            writer.writerow({
                "id": props.get("id", ""),
                "lat": coords[1],
                "lon": coords[0],
                "layer": "address",
                "source": "overture",
                "housenumber": props.get("number", ""),
                "street": props.get("street", ""),
                "postcode": props.get("postcode", ""),
                "city": props.get("city", ""),
                "country": props.get("country", ""),
            })


def download_overture_data(
        bbox: tuple[float, float, float, float],
        output_path: str) -> bool:
    os.makedirs(output_path, exist_ok=True)

    places_geojson = os.path.join(output_path, "overture-places.geojson")
    places_csv = os.path.join(output_path, "overture-places.csv")
    if not _download_overture_theme(bbox, "place", places_geojson):
        return False
    _convert_overture_places_to_csv(places_geojson, places_csv)
    os.remove(places_geojson)

    addresses_geojson = os.path.join(output_path, "overture-addresses.geojson")
    addresses_csv = os.path.join(output_path, "overture-addresses.csv")
    if not _download_overture_theme(bbox, "address", addresses_geojson):
        return False
    _convert_overture_addresses_to_csv(addresses_geojson, addresses_csv)
    os.remove(addresses_geojson)

    return True


def import_pelias_csv(
        csv_tools_path: str,
        build_path: str) -> bool:
    config_file_path = os.path.join(build_path, "pelias.json")

    cmd0 = ["npm", "install", "pelias-config"]
    cmd1 = ["./bin/start"]

    env = os.environ.copy()
    env["PELIAS_CONFIG"] = config_file_path

    try:
        subprocess.run(cmd0, cwd=csv_tools_path, check=True)
        subprocess.run(cmd1, cwd=csv_tools_path, env=env, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    return True


def download_gtfs_feeds(
        feeds: list[str],
        output_path: str) -> bool:
    os.makedirs(output_path, exist_ok=True)
    for i, url in enumerate(feeds):
        out_file = os.path.join(output_path, f"feed_{i:03d}.zip")
        if os.path.exists(out_file):
            print(f"    Skipping {url} (already downloaded)")
            continue
        try:
            subprocess.run(
                ["curl", "-L", "-o", out_file, url],
                check=True)
        except subprocess.CalledProcessError as ex:
            print(f"    Failed to download {url}: {ex}")
            return False
    return True


def import_pelias_transit(
        transit_tools_path: str,
        build_path: str) -> bool:
    config_file_path = os.path.join(build_path, "pelias.json")

    cmd0 = ["npm", "install", "pelias-config"]
    cmd1 = ["./bin/start"]

    env = os.environ.copy()
    env["PELIAS_CONFIG"] = config_file_path

    try:
        subprocess.run(cmd0, cwd=transit_tools_path, check=True)
        subprocess.run(cmd1, cwd=transit_tools_path, env=env, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    return True


def archive_pelias_wof_data(
        region_name: str,
        build_path: str,
        result_path: str) -> tuple[bool, str]:

    out_file = os.path.join(result_path, "wof.tar.gz")
    wof_data_path = os.path.join(build_path,
                                 "data", "wof", region_name)

    if os.path.exists(out_file):
        os.remove(out_file)

    cmd = f"tar -czf \"{out_file}\" -C \"{wof_data_path}\" ."

    try:
        subprocess.run(cmd, cwd=build_path, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return (False, None)

    return (True, out_file)


def _es_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _es_request(method: str, url: str,
                data: dict = None,
                timeout: int = 30) -> tuple[bool, dict]:
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return (True, result)
    except Exception as ex:
        print(f"ES request failed: {method} {url} — {ex}")
        return (False, None)


def wait_for_elasticsearch(host: str, port: int,
                           timeout: int = 120) -> bool:
    base = _es_url(host, port)
    url = f"{base}/_cluster/health?wait_for_status=green&timeout={timeout}s"
    print(f"  Waiting for Elasticsearch at {base} (timeout {timeout}s)")
    start = time.time()
    while time.time() - start < timeout:
        ok, result = _es_request("GET", url)
        if ok and result.get("status") in ("green", "yellow"):
            print(f"  Elasticsearch ready (status: {result['status']})")
            return True
        time.sleep(3)
    print(f"  Elasticsearch not ready after {timeout}s")
    return False


def create_snapshot_repository(
        host: str, port: int,
        repo_name: str,
        repo_path: str) -> bool:
    base = _es_url(host, port)
    url = f"{base}/_snapshot/{repo_name}"
    payload = {
        "type": "fs",
        "settings": {
            "location": repo_path,
            "compress": True
        }
    }
    print(f"  Registering snapshot repository '{repo_name}' at {repo_path}")
    ok, result = _es_request("PUT", url, payload)
    if not ok:
        return False
    if not result.get("acknowledged", False):
        print(f"  Snapshot repository registration not acknowledged: {result}")
        return False
    return True


def snapshot_indices(
        host: str, port: int,
        repo_name: str,
        snapshot_name: str,
        indices: list[str]) -> bool:
    base = _es_url(host, port)
    url = (f"{base}/_snapshot/{repo_name}/{snapshot_name}"
           f"?wait_for_completion=true")
    payload = {
        "indices": ",".join(indices),
        "ignore_unavailable": True,
        "include_global_state": False
    }
    print(f"  Creating snapshot '{snapshot_name}' of {len(indices)} indices")
    ok, result = _es_request("PUT", url, payload, timeout=3600)
    if not ok:
        return False
    state = result.get("snapshot", {}).get("state", "UNKNOWN")
    if state != "SUCCESS":
        print(f"  Snapshot state: {state}")
        if state == "PARTIAL":
            print("  Warning: snapshot is partial (some shards failed)")
        else:
            return False
    print(f"  Snapshot '{snapshot_name}' completed ({state})")
    return True


def create_alias(
        host: str, port: int,
        index_name: str,
        alias_name: str) -> bool:
    base = _es_url(host, port)
    url = f"{base}/_aliases"
    payload = {
        "actions": [
            {"add": {"index": index_name, "alias": alias_name}}
        ]
    }
    print(f"  Creating alias '{alias_name}' → '{index_name}'")
    ok, result = _es_request("POST", url, payload)
    if not ok:
        return False
    if not result.get("acknowledged", False):
        print(f"  Alias creation not acknowledged: {result}")
        return False
    return True


def switch_alias(
        host: str, port: int,
        old_index: str,
        new_index: str,
        alias_name: str) -> bool:
    base = _es_url(host, port)
    url = f"{base}/_aliases"
    actions = [{"add": {"index": new_index, "alias": alias_name}}]
    if old_index:
        actions.insert(0,
                       {"remove": {"index": old_index, "alias": alias_name}})
    payload = {"actions": actions}
    print(f"  Switching alias '{alias_name}': {old_index} → {new_index}")
    ok, result = _es_request("POST", url, payload)
    if not ok:
        return False
    if not result.get("acknowledged", False):
        print(f"  Alias switch not acknowledged: {result}")
        return False
    return True


def generate_prod_config(
        region_name: str,
        result_path: str,
        pelias_config: Pelias,
        es_host: str = None,
        es_port: int = None) -> bool:
    if es_host is None:
        es_host = pelias_config.elasticsearch_host_prod()
    if es_port is None:
        es_port = pelias_config.elasticsearch_port_prod()

    wof_data_path = "/data/whosonfirst"

    template_data = {
        "es_api_version": pelias_config.elasticsearch_api_version(),
        "es_host": es_host,
        "es_port": es_port,
        "region_name": region_name,
        "placeholder_url": "http://pelias-placeholder:3000",
        "libpostal_url": "http://pelias-libpostal:4400",
        "pip_url": f"http://pelias-{region_name}-pip:3102",
        "wof_data_path": wof_data_path,
    }

    template_path = pelias_config.config_prod_template_file_path()
    if not os.path.exists(template_path):
        print(f"  Prod config template not found: {template_path}")
        return False

    with open(template_path, "r") as f:
        template = Template(f.read())

    json_string = template.substitute(**template_data)
    output_path = os.path.join(result_path,
                               f"pelias-prod-{region_name}.json")
    with open(output_path, "w") as f:
        f.write(json_string)

    print(f"  Generated prod config: {output_path}")
    return True
