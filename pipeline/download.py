import os
import requests
import subprocess
import gzip
import shutil


def download_file(
        url: str,
        path: str,
        file: str):
    os.makedirs(path, exist_ok=True)
    target_path = os.path.join(path, file)

    if os.path.exists(target_path):
        print(f"File '{target_path}' already exists. Skipping download.")
        return

    response = requests.get(url, stream=True)
    response.raise_for_status()
    with open(target_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    print(f"Downloaded '{url}' to '{target_path}'")


def s3_download_and_unpack_gz(
        url: str,
        path: str,
        gz_file: str,
        subdir: str,
        unpacked_file: str):
    os.makedirs(path, exist_ok=True)
    target_gz_path = os.path.join(path, gz_file)
    target_dir = os.path.join(path, subdir)
    target_file = os.path.join(target_dir, unpacked_file)

    if os.path.exists(target_file):
        print(f"File '{target_file}' already exists. Skipping download.")
        return

    os.makedirs(target_dir, exist_ok=True)

    cmd = ["aws", "s3", "cp", "--no-sign-request",
           f"{url}", f"{target_gz_path}"]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as ex:
        print(ex)
        return False

    with gzip.open(
                target_gz_path, "rb"
            ) as f_in, open(
                target_file, "wb"
            ) as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(target_gz_path)

    print(f"Downloaded '{url}' to '{target_file}'")
