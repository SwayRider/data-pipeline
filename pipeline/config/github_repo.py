import logging
import os
import re
import subprocess

# Prevent git from prompting for credentials in non-interactive pipelines
_NO_PROMPT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


logger = logging.getLogger(__name__)


class GithubRepo:
    def __init__(self, base_url: str, name_and_tag: str):
        self.base_url = base_url
        parts = name_and_tag.split(":")
        self.name = parts[0]
        if len(parts) > 1:
            self.tag = parts[1]
        else:
            self.tag = "master"
        # resolved_tag holds the actual tag used for cloning; equals self.tag
        # unless self.tag is "latest", in which case it is resolved at pull() time.
        self.resolved_tag = None
        self.submodules = False
        self.cmake_build = False
        self.cmake_build_opts = []
        self.cmake_compile = False
        self.cmake_install = False
        self.make_build = False
        self.make_build_opts = ["all"]
        self.make_install = False
        self.npm_install = False
        self.npm_extra_pkgs = []

    def set_init_submodules(self):
        self.submodules = True

    def set_cmake_build(self, opts: list[str]):
        self.cmake_build = True
        self.cmake_build_opts = opts

    def set_cmake_compile(self):
        self.cmake_compile = True

    def set_cmake_install(self):
        self.cmake_install = True

    def set_make_build(self, opts: list[str]):
        self.make_build = True
        self.make_build_opts = opts

    def set_make_install(self):
        self.make_install = True

    def set_npm_install(self):
        self.npm_install = True

    def set_npm_extra_pkgs(self, pkgs: list[str]):
        self.npm_extra_pkgs = pkgs

    def _default_branch(self) -> str:
        """Detect the default branch of the remote repository.

        Runs ``git ls-remote --symref <url> HEAD`` and parses the
        ``ref: refs/heads/<branch>`` line. Returns ``"main"`` if detection
        fails.
        """
        repo_url = f"{self.base_url}/{self.name}"
        try:
            result = subprocess.run(
                ["git", "ls-remote", "--symref", repo_url, "HEAD"],
                capture_output=True, text=True, check=True,
                env=_NO_PROMPT_ENV)
        except subprocess.CalledProcessError:
            return "main"

        for line in result.stdout.splitlines():
            if line.startswith("ref: refs/heads/"):
                return line.split("ref: refs/heads/")[1].split("\t")[0].strip()
        return "main"

    def _resolve_latest_tag(self) -> str:
        """Query GitHub for the most recent version tag without cloning.

        Runs ``git ls-remote --tags --sort=-version:refname <repo_url>`` and
        returns the first tag that looks like a version string (starts with
        ``v`` or a digit, ignoring ``^{}`` dereference suffixes).

        Returns the resolved tag string (e.g. ``v3.6.3``) on success, or
        the detected default branch as a fallback when resolution fails.
        """
        repo_url = f"{self.base_url}/{self.name}"
        try:
            result = subprocess.run(
                ["git", "ls-remote", "--tags", "--sort=-version:refname",
                 repo_url],
                capture_output=True,
                text=True,
                check=True,
                env=_NO_PROMPT_ENV,
            )
        except subprocess.CalledProcessError as ex:
            fallback = self._default_branch()
            logger.warning(
                "ls-remote failed for %s (exit %d): %s — falling back to %s",
                repo_url, ex.returncode, ex.stderr.strip(), fallback,
            )
            return fallback

        # Each line: "<sha>\trefs/tags/<tag>"
        # Skip peeled tag derefs (ending in ^{}) and pre-release tags (rc, alpha, beta, pre).
        version_re = re.compile(r"refs/tags/(v?\d+\.\d+[\.\d]*)$")
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            ref = parts[1]
            match = version_re.search(ref)
            if match:
                return match.group(1)

        fallback = self._default_branch()
        logger.warning(
            "No version tags found for %s — falling back to %s",
            repo_url, fallback,
        )
        return fallback

    def pull(self, base_dir: str) -> str:
        """Clone the repository into *base_dir* and return the clone path.

        When ``self.tag`` is ``"latest"``, the actual release tag is resolved
        via ``git ls-remote`` before cloning. The resolved tag is stored in
        ``self.resolved_tag`` so callers can inspect it. ``self.tag`` is left
        as ``"latest"`` as specified in the config.

        If the tag is not ``"latest"``, ``self.resolved_tag`` is set to
        ``self.tag`` and behaviour is unchanged from the original
        implementation.

        The clone directory is named ``<name>_<resolved_tag>`` so that re-runs
        reuse an already-built version without re-cloning or rebuilding.
        """
        if self.tag == "latest":
            if self.resolved_tag is None:
                self.resolved_tag = self._resolve_latest_tag()
                logger.info(
                    "%s: resolved 'latest' -> %s", self.name, self.resolved_tag
                )
        else:
            self.resolved_tag = self.tag

        dest_dir = os.path.join(base_dir, self.name + "_" + self.resolved_tag)
        if os.path.exists(dest_dir):
            logger.info("%s: reusing existing clone at %s", self.name, dest_dir)
            return dest_dir

        clone_branch = self.resolved_tag
        os.makedirs(dest_dir, exist_ok=True)
        try:
            subprocess.run(
                    ["git", "clone", "--branch", clone_branch, "--single-branch",
                        "--depth", "1", f"{self.base_url}/{self.name}", dest_dir],
                    check=True, env=_NO_PROMPT_ENV)
        except subprocess.CalledProcessError:
            import shutil
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise

        if self.submodules:
            subprocess.run(
                    ["git", "submodule", "update", "--init", "--recursive"],
                    cwd=dest_dir, check=True)

        return dest_dir

    def install(self, base_dir: str) -> bool:
        # Use resolved_tag (set by pull()) so the correct directory is found
        # when self.tag is "latest".
        effective_tag = self.resolved_tag if self.resolved_tag is not None else self.tag
        source_dir = os.path.join(base_dir, self.name + "_" + effective_tag)

        if self.cmake_build:
            res = self._cmake_build(source_dir)
            if not res:
                return False

        if self.cmake_compile:
            res = self._cmake_compile(source_dir)
            if not res:
                return False

        if self.cmake_install:
            res = self._cmake_install(source_dir)
            if not res:
                return False

        if self.make_build:
            res = self._make_build(source_dir)
            if not res:
                return False

        if self.make_install:
            res = self._make_install(source_dir)
            if not res:
                return False

        if self.npm_install:
            res = self._npm_install(source_dir)
            if not res:
                return False

        return True

    def _cmake_build(self, source_dir: str) -> bool:
        build_dir = os.path.join(source_dir, "build")
        os.makedirs(build_dir, exist_ok=True)

        cmd = ["cmake"]
        if len(self.cmake_build_opts) > 0:
            cmd += self.cmake_build_opts
        cmd += [".."]

        try:
            subprocess.run(cmd, cwd=build_dir, check=True)
        except subprocess.CalledProcessError as ex:
            print(ex)
            return False

        return True

    def _cmake_compile(self, source_dir: str) -> bool:
        build_dir = os.path.join(source_dir, "build")

        try:
            subprocess.run(
                    ["cmake", "--build", "."],
                    cwd=build_dir, check=True)
        except subprocess.CalledProcessError as ex:
            print(ex)
            return False

        return True

    def _cmake_install(self, source_dir: str) -> bool:
        build_dir = os.path.join(source_dir, "build")

        try:
            subprocess.run(
                    ["cmake", "--build", ".", "--target", "install"],
                    cwd=build_dir, check=True)
        except subprocess.CalledProcessError as ex:
            print(ex)
            return False

        return True

    def _make_build(self, source_dir: str) -> bool:
        build_dir = source_dir
        if self.cmake_build:
            build_dir = os.path.join(source_dir, "build")

        cmd = ["make"]
        if len(self.make_build_opts) > 0:
            cmd += self.make_build_opts
        try:
            subprocess.run(cmd, cwd=build_dir, check=True)
        except subprocess.CalledProcessError as ex:
            print(ex)
            return False
        return True

    def _make_install(self, source_dir: str) -> bool:
        build_dir = source_dir
        if self.cmake_build:
            build_dir = os.path.join(source_dir, "build")

        try:
            subprocess.run(
                    ["make", "install"],
                    cwd=build_dir, check=True)
        except subprocess.CalledProcessError as ex:
            print(ex)
            return False

        return True

    def _npm_install(self, source_dir: str) -> bool:
        try:
            subprocess.run(
                    ["npm", "install", "--save-dev", "prebuild-install"],
                    cwd=source_dir, check=True)
        except subprocess.CalledProcessError as ex:
            print(ex)

        try:
            subprocess.run(
                    ["npm", "install"],
                    cwd=source_dir, check=True)
        except subprocess.CalledProcessError as ex:
            print(ex)
            return False

        for pkg in self.npm_extra_pkgs:
            try:
                subprocess.run(
                        ["npm", "install", pkg],
                        cwd=source_dir, check=True)
            except subprocess.CalledProcessError as ex:
                print(ex)
                return False

        return True
