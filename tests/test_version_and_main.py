from __future__ import annotations

import runpy
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

from eve_client import _version


def test_resolve_version_prefers_installed_package() -> None:
    with patch("eve_client._version.installed_version", return_value="9.9.9"):
        assert _version.resolve_version() == "9.9.9"


def test_version_from_pyproject_fallback(tmp_path: Path) -> None:
    package_root = tmp_path / "packages" / "client"
    package_root.mkdir(parents=True)
    pyproject_path = package_root / "pyproject.toml"
    pyproject_path.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    fake_module_path = package_root / "eve_client" / "_version.py"
    fake_module_path.parent.mkdir(parents=True)
    fake_module_path.write_text("", encoding="utf-8")

    with (
        patch("eve_client._version.installed_version", side_effect=PackageNotFoundError),
        patch("eve_client._version.Path.resolve", return_value=fake_module_path),
    ):
        assert _version.resolve_version() == "1.2.3"


def test_module_entrypoint_calls_cli_main() -> None:
    with patch("eve_client.cli.main") as main:
        runpy.run_module("eve_client.__main__", run_name="__main__")
    main.assert_called_once_with()
