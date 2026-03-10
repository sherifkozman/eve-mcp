from __future__ import annotations

import os
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path


def _version_from_pyproject() -> str | None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if not pyproject_path.exists():
        return None
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def resolve_version() -> str:
    build_version = os.environ.get("EVE_CLIENT_BUILD_VERSION")
    if build_version:
        return build_version
    try:
        return installed_version("eve-client")
    except PackageNotFoundError:
        return _version_from_pyproject() or "0.0.0+unknown"


__version__ = resolve_version()
