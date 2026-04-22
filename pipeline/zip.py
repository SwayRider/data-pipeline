import zipfile


def unzip_file(zip_file: str, to_path: str):
    with zipfile.ZipFile(zip_file, "r") as zip_ref:
        zip_ref.extractall(to_path)
