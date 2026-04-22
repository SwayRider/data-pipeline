import os
import sys
import time


class ProgressStream:
    def __init__(self, file_path: str, label: str = ""):
        self.file_path = file_path
        self.label = label or os.path.basename(file_path)
        self.f = open(file_path, "rb")
        self.total = os.path.getsize(file_path)
        self.read_bytes = 0
        self.last_update = time.time()

    def read(self, size: int):
        chunk = self.f.read(size)
        if not chunk:
            sys.stdout.write("\n")
            return chunk

        self.read_bytes += len(chunk)
        self.print_progress()
        return chunk

    def print_progress(self):
        now = time.time()
        if now - self.last_update < 0.1:
            return

        self.last_update = now
        pct = (self.read_bytes / self.total) * 100
        done = self.read_bytes / (1024 * 1024)
        total = self.total / (1024 * 1024)
        sys.stdout.write(f"\r    {self.label} {done:.2f} MB of {total:.2f} MB ({pct:.2f}%)")
        sys.stdout.flush()

    def seek(self, offset: int, whence: int = 0):
        return self.f.seek(offset, whence)

    def close(self):
        pct = (self.read_bytes / self.total) * 100
        done = self.read_bytes / (1024 * 1024)
        total = self.total / (1024 * 1024)
        sys.stdout.write(f"\r    {self.label} {done:.2f} MB of {total:.2f} MB ({pct:.2f}%)")
        sys.stdout.write("\n")
        sys.stdout.flush()
        return self.f.close()
