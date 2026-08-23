"""Build CookSprite's browser and Agent assets into the Python wheel."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent


class CookSpriteBuild(build_py):
    def run(self) -> None:
        # Release builds consume the already-tested static bundle shipped in
        # ``cooksprite/static``.  Rebuilding Vue during every wheel build
        # would require Node/network access and make a Python package build
        # needlessly slow.  Contributors can opt into a fresh bundle with
        # ``COOKSPRITE_BUILD_WEB=1`` before packaging.
        if os.environ.get("COOKSPRITE_BUILD_WEB") == "1":
            subprocess.run(["npm", "ci"], cwd=ROOT / "web", check=True)
            subprocess.run(["npm", "run", "build"], cwd=ROOT / "web", check=True)
        super().run()
        package_root = Path(self.build_lib) / "cooksprite"
        built_web = ROOT / "web" / "dist"
        if built_web.is_dir():
            shutil.copytree(built_web, package_root / "static", dirs_exist_ok=True)
        skill_root = package_root / "skill"
        skill_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "skills" / "cooksprite" / "SKILL.md", skill_root / "SKILL.md")
        shutil.copy2(
            ROOT / "skills" / "cooksprite" / "ACTIONS.generated.md",
            skill_root / "ACTIONS.generated.md",
        )


setup(cmdclass={"build_py": CookSpriteBuild})
