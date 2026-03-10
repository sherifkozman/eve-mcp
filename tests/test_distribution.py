from __future__ import annotations

import os
import subprocess
import tarfile
import venv
import zipfile
from importlib.metadata import version as installed_version
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPO_ROOT / "packages" / "client"
INSTALL_SCRIPT = PACKAGE_ROOT / "scripts" / "install-eve-client.sh"


def _run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd or REPO_ROOT),
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )


def test_built_wheel_contains_expected_runtime_files(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    _run("uv", "build", str(PACKAGE_ROOT), "--out-dir", str(dist_dir))
    wheel_path = next(dist_dir.glob("eve_client-*.whl"))
    package_version = installed_version("eve-client")

    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())
        dist_info = f"eve_client-{package_version}.dist-info"
        entry_points = wheel.read(f"{dist_info}/entry_points.txt").decode("utf-8")
        metadata = wheel.read(f"{dist_info}/METADATA").decode("utf-8")

    assert "eve_client/__main__.py" in names
    assert "eve_client/cli.py" in names
    assert "eve_client/tests/test_cli.py" not in names
    assert "eve = eve_client.cli:main" in entry_points
    assert f"Version: {package_version}" in metadata


def test_built_sdist_contains_readme_and_package_sources(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    _run("uv", "build", str(PACKAGE_ROOT), "--out-dir", str(dist_dir))
    sdist_path = next(dist_dir.glob("eve_client-*.tar.gz"))

    with tarfile.open(sdist_path, "r:gz") as sdist:
        names = set(sdist.getnames())

    root_prefix = f"eve_client-{installed_version('eve-client')}"
    assert f"{root_prefix}/README.md" in names
    assert f"{root_prefix}/pyproject.toml" in names
    assert f"{root_prefix}/eve_client/__main__.py" in names


def test_installed_wheel_exposes_eve_entrypoint_and_module_entrypoint(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    _run("uv", "build", str(PACKAGE_ROOT), "--out-dir", str(dist_dir))
    wheel_path = next(dist_dir.glob("eve_client-*.whl"))

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(venv_dir)
    venv_python = venv_dir / "bin" / "python"
    venv_eve = venv_dir / "bin" / "eve"

    _run(str(venv_python), "-m", "pip", "install", str(wheel_path), cwd=tmp_path)

    eve_result = _run(str(venv_eve), "version", cwd=tmp_path)
    module_result = _run(str(venv_python), "-m", "eve_client", "version", cwd=tmp_path)

    expected = installed_version("eve-client")
    assert eve_result.stdout.strip() == expected
    assert module_result.stdout.strip() == expected


def test_install_script_installs_local_package_and_verifies_binary_with_explicit_shadow_override(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cache = tmp_path / "cache"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    cache.mkdir()
    fake_bin.mkdir()
    (fake_bin / "eve").write_text("#!/usr/bin/env bash\necho fake-eve\n", encoding="utf-8")
    (fake_bin / "eve").chmod(0o755)
    env = {
        "HOME": str(home),
        "UV_CACHE_DIR": str(cache),
        "EVE_CLIENT_SOURCE": str(PACKAGE_ROOT),
        "EVE_CLIENT_INSTALL_FLAGS": "--force",
        "EVE_CLIENT_ALLOW_SHADOWED_BINARY": "1",
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }

    install_result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT)],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=True,
        env={**os.environ, **env},
    )
    uv_bin_dir = _run("uv", "tool", "dir", "--bin", cwd=REPO_ROOT, env={**os.environ, **env}).stdout.strip()
    eve_binary = Path(uv_bin_dir) / "eve"

    assert "Installed executable:" in install_result.stdout
    assert str(eve_binary) in install_result.stdout
    assert eve_binary.exists()
    assert "SECURITY WARNING:" in install_result.stderr
    assert "currently resolves eve to" in install_result.stderr
    assert "Proceeding because EVE_CLIENT_ALLOW_SHADOWED_BINARY=1 is set." in install_result.stderr
    version_result = subprocess.run(
        [str(eve_binary), "version"],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=True,
        env={**os.environ, **env},
    )
    assert version_result.stdout.strip() == installed_version("eve-client")


def test_install_script_fails_closed_on_shadowed_binary_in_non_interactive_mode(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cache = tmp_path / "cache"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    cache.mkdir()
    fake_bin.mkdir()
    (fake_bin / "eve").write_text("#!/usr/bin/env bash\necho fake-eve\n", encoding="utf-8")
    (fake_bin / "eve").chmod(0o755)

    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT)],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HOME": str(home),
            "UV_CACHE_DIR": str(cache),
            "EVE_CLIENT_SOURCE": str(PACKAGE_ROOT),
            "EVE_CLIENT_INSTALL_FLAGS": "--force",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode != 0
    assert "SECURITY WARNING:" in result.stderr
    assert "Aborting because a conflicting eve binary is ahead of the installed one on PATH." in result.stderr
    assert "EVE_CLIENT_ALLOW_SHADOWED_BINARY=1" in result.stderr


def test_install_script_can_still_force_fail_on_shadowed_binary_override_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cache = tmp_path / "cache"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    cache.mkdir()
    fake_bin.mkdir()
    (fake_bin / "eve").write_text("#!/usr/bin/env bash\necho fake-eve\n", encoding="utf-8")
    (fake_bin / "eve").chmod(0o755)

    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT)],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HOME": str(home),
            "UV_CACHE_DIR": str(cache),
            "EVE_CLIENT_SOURCE": str(PACKAGE_ROOT),
            "EVE_CLIENT_INSTALL_FLAGS": "--force",
            "EVE_CLIENT_ALLOW_SHADOWED_BINARY": "1",
            "EVE_CLIENT_FAIL_ON_SHADOWED_BINARY": "1",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode != 0
    assert "SECURITY WARNING:" in result.stderr
    assert "EVE_CLIENT_FAIL_ON_SHADOWED_BINARY=1 overrides EVE_CLIENT_ALLOW_SHADOWED_BINARY=1." in result.stderr
    assert "Aborting because EVE_CLIENT_FAIL_ON_SHADOWED_BINARY=1 is set." in result.stderr


def test_install_script_force_fail_env_blocks_shadowed_binary_without_allow(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cache = tmp_path / "cache"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    cache.mkdir()
    fake_bin.mkdir()
    (fake_bin / "eve").write_text("#!/usr/bin/env bash\necho fake-eve\n", encoding="utf-8")
    (fake_bin / "eve").chmod(0o755)

    result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT)],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HOME": str(home),
            "UV_CACHE_DIR": str(cache),
            "EVE_CLIENT_SOURCE": str(PACKAGE_ROOT),
            "EVE_CLIENT_INSTALL_FLAGS": "--force",
            "EVE_CLIENT_FAIL_ON_SHADOWED_BINARY": "1",
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode != 0
    assert "SECURITY WARNING:" in result.stderr
    assert "Aborting because EVE_CLIENT_FAIL_ON_SHADOWED_BINARY=1 is set." in result.stderr
