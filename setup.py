"""Build CookSprite's browser and Agent assets into the Python wheel."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent


class CookSpriteBuild(build_py):
    def run(self) -> None:
        subprocess.run(["npm", "ci"], cwd=ROOT / "web", check=True)
        subprocess.run(["npm", "run", "build"], cwd=ROOT / "web", check=True)
        super().run()
        package_root = Path(self.build_lib) / "cooksprite"
        shutil.copytree(ROOT / "web" / "dist", package_root / "static", dirs_exist_ok=True)
        skill_root = package_root / "skill"
        skill_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "skills" / "cooksprite" / "SKILL.md", skill_root / "SKILL.md")
        shutil.copy2(
            ROOT / "skills" / "cooksprite" / "ACTIONS.generated.md",
            skill_root / "ACTIONS.generated.md",
        )


setup(cmdclass={"build_py": CookSpriteBuild})
