import os
import subprocess
import shutil


def cleanup_valhalla_data(build_path: str) -> bool:
    tiles_dir = os.path.join(build_path, "valhalla", "tiles")
    tiles_tar_file = os.path.join(build_path, "valhalla", "tiles.tar")
    config_file = os.path.join(build_path, "valhalla.json")

    try:
        subprocess.run(f"rm -rf \"{tiles_dir}\"", shell=True, check=True)
        subprocess.run(f"rm -f \"{tiles_tar_file}\"", shell=True, check=True)
        subprocess.run(f"rm -f \"{config_file}\"", shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    return True


def create_valhalla_config(
        build_path: str,
        tool_path: str,
        srtm_path: str) -> bool:
    tiles_path = os.path.join(build_path, "valhalla", "tiles")
    tiles_tar_file = os.path.join(build_path, "valhalla", "tiles.tar")
    admin_path = os.path.join(build_path, "valhalla", "admin.sqlite")
    tz_path = os.path.join(build_path, "valhalla", "tz_world.sqlite")
    config_path = os.path.join(build_path, "valhalla.json")

    tool = os.path.join(tool_path, "valhalla_build_config")

    cmd = f"\"{tool}\""
    cmd += f" --mjolnir-tile-dir \"{tiles_path}\""
    cmd += f" --mjolnir-tile-extract \"{tiles_tar_file}\""
    cmd += f" --mjolnir-admin \"{admin_path}\""
    cmd += f" --mjolnir-timezone \"{tz_path}\""
    cmd += f" --additional-data-elevation \"{srtm_path}\""
    cmd += f" > \"{config_path}\""

    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    return True


def build_valhalla_tiles(
        region_name: str,
        build_path: str,
        tool_path: str,
        srtm_path: str,
        input_osm_dir: str,
        input_osm: str,
        result_path: str) -> tuple[bool, str, str, str, str]:

    tiles_tar_file = os.path.join(build_path, "valhalla", "tiles.tar")
    admin_path = os.path.join(build_path, "valhalla", "admin.sqlite")
    tz_path = os.path.join(build_path, "valhalla", "tz_world.sqlite")
    config_path = os.path.join(build_path, "valhalla.json")
    osm_file = os.path.join(input_osm_dir, input_osm)

    output_path = os.path.join(result_path, "valhalla", region_name)
    os.makedirs(output_path, exist_ok=True)

    output_tiles_tar_file = os.path.join(output_path, "tiles.tar")
    output_admin_path = os.path.join(output_path, "admin.sqlite")
    output_tz_path = os.path.join(output_path, "tz_world.sqlite")

    admin_tool = os.path.join(tool_path, "valhalla_build_admins")
    tz_tool = os.path.join(tool_path, "valhalla_build_timezones")
    tiles_tool = os.path.join(tool_path, "valhalla_build_tiles")
    extract_tool = os.path.join(tool_path, "valhalla_build_extract")

    if os.path.exists(output_tiles_tar_file):
        print(f"File {output_tiles_tar_file} already exists. Skipping.")
        return False, None, None, None, None

    print("    - admin.sqlite")
    cmd = f"\"{admin_tool}\" --config \"{config_path}\""
    cmd += f" \"{osm_file}\""
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False, None, None, None, None

    print("    - tz_world.sqlite")
    cmd = f"\"{tz_tool}\" > \"{tz_path}\""
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False, None, None, None, None

    print("    - tiles")
    cmd = f"\"{tiles_tool}\" --config \"{config_path}\""
    cmd += " --concurrency=4"
    cmd += f" \"{osm_file}\""
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False, None, None, None, None

    print("    - tiles.tar")
    cmd = f"\"{extract_tool}\" --config \"{config_path}\""
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False, None, None, None, None

    shutil.copyfile(admin_path, output_admin_path)
    shutil.copyfile(tz_path, output_tz_path)
    shutil.copyfile(tiles_tar_file, output_tiles_tar_file)

    return True, output_path, "tiles.tar", "admin.sqlite", "tz_world.sqlite"
