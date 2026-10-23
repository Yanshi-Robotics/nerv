"""Include prebuilt UI and product defaults in the installed host wheel."""
from pathlib import Path
import shutil
import uuid
from setuptools import setup
from setuptools.command.build import build
from setuptools.command.build_py import build_py


class IsolatedBuild(build):
    def finalize_options(self):
        self.build_base = str(Path(__file__).parent / "temp" / "release" / "builds" / uuid.uuid4().hex)
        super().finalize_options()


class BuildWithResources(build_py):
    def run(self):
        super().run()
        root = Path(__file__).parent
        web = root / "frontend" / "out"
        if not (web / "index.html").is_file():
            raise RuntimeError("Build the static UI before building a release wheel")
        package = Path(self.build_lib) / "nerv"
        shutil.copytree(web, package / "web", dirs_exist_ok=True)
        profile = package / "registry" / "bodies" / "arm-lerobot-so101"
        profile.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "packages/nerv-runtime-lerobot/src/nerv_runtime_lerobot/product.json", profile / "body.yaml")


setup(cmdclass={"build": IsolatedBuild, "build_py": BuildWithResources})
