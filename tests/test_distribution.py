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
PUBLISH_SCRIPT = PACKAGE_ROOT / "scripts" / "publish-eve-client-pypi.sh"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-eve-client.yml"


def _run(
    *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
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


def test_install_script_installs_local_package_and_verifies_binary_with_explicit_shadow_override(
    tmp_path: Path,
) -> None:
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
    uv_bin_dir = _run(
        "uv", "tool", "dir", "--bin", cwd=REPO_ROOT, env={**os.environ, **env}
    ).stdout.strip()
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


def test_install_script_fails_closed_on_shadowed_binary_in_non_interactive_mode(
    tmp_path: Path,
) -> None:
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
    assert (
        "Aborting because a conflicting eve binary is ahead of the installed one on PATH."
        in result.stderr
    )
    assert "EVE_CLIENT_ALLOW_SHADOWED_BINARY=1" in result.stderr


def test_install_script_can_still_force_fail_on_shadowed_binary_override_path(
    tmp_path: Path,
) -> None:
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
    assert (
        "EVE_CLIENT_FAIL_ON_SHADOWED_BINARY=1 overrides EVE_CLIENT_ALLOW_SHADOWED_BINARY=1."
        in result.stderr
    )
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


def test_publish_script_dry_run_checks_artifacts_without_uploading(tmp_path: Path) -> None:
    assert PUBLISH_SCRIPT.exists()
    dist_dir = tmp_path / "dist"
    bin_dir = tmp_path / "bin"
    log_path = tmp_path / "commands.log"
    dist_dir.mkdir()
    bin_dir.mkdir()
    (dist_dir / "eve_client-0.0.0.tar.gz").write_text("sdist", encoding="utf-8")
    (dist_dir / "eve_client-0.0.0-py3-none-any.whl").write_text("wheel", encoding="utf-8")
    (bin_dir / "uv").write_text(
        "#!/usr/bin/env bash\n"
        "echo uv \"$@\" >> \"$EVE_TEST_COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    (bin_dir / "uvx").write_text(
        "#!/usr/bin/env bash\n"
        "echo uvx \"$@\" >> \"$EVE_TEST_COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    (bin_dir / "uv").chmod(0o755)
    (bin_dir / "uvx").chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            str(PUBLISH_SCRIPT),
            "--dry-run",
            "--skip-build",
            "--dist-dir",
            str(dist_dir),
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "EVE_TEST_COMMAND_LOG": str(log_path),
        },
    )

    command_log = log_path.read_text(encoding="utf-8")
    assert "uvx twine check" in command_log
    assert "uv publish" not in command_log
    assert "Dry run complete; not publishing" in result.stdout


def test_publish_script_default_mode_checks_artifacts_without_uploading(tmp_path: Path) -> None:
    assert PUBLISH_SCRIPT.exists()
    dist_dir = tmp_path / "dist"
    bin_dir = tmp_path / "bin"
    log_path = tmp_path / "commands.log"
    dist_dir.mkdir()
    bin_dir.mkdir()
    (dist_dir / "eve_client-0.0.0.tar.gz").write_text("sdist", encoding="utf-8")
    (dist_dir / "eve_client-0.0.0-py3-none-any.whl").write_text("wheel", encoding="utf-8")
    (bin_dir / "uv").write_text(
        "#!/usr/bin/env bash\n"
        "echo uv \"$@\" >> \"$EVE_TEST_COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    (bin_dir / "uvx").write_text(
        "#!/usr/bin/env bash\n"
        "echo uvx \"$@\" >> \"$EVE_TEST_COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    (bin_dir / "uv").chmod(0o755)
    (bin_dir / "uvx").chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            str(PUBLISH_SCRIPT),
            "--skip-build",
            "--dist-dir",
            str(dist_dir),
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "EVE_TEST_COMMAND_LOG": str(log_path),
        },
    )

    command_log = log_path.read_text(encoding="utf-8")
    assert "uvx twine check" in command_log
    assert "uv publish" not in command_log
    assert "Dry run complete; not publishing" in result.stdout


def test_publish_script_requires_token_for_real_publish(tmp_path: Path) -> None:
    assert PUBLISH_SCRIPT.exists()
    dist_dir = tmp_path / "dist"
    bin_dir = tmp_path / "bin"
    dist_dir.mkdir()
    bin_dir.mkdir()
    (dist_dir / "eve_client-0.0.0.tar.gz").write_text("sdist", encoding="utf-8")
    (dist_dir / "eve_client-0.0.0-py3-none-any.whl").write_text("wheel", encoding="utf-8")
    (bin_dir / "uvx").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (bin_dir / "uvx").chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            str(PUBLISH_SCRIPT),
            "--publish",
            "--skip-build",
            "--dist-dir",
            str(dist_dir),
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PYPI_API_TOKEN": "",
        },
    )

    assert result.returncode != 0
    assert "PYPI_API_TOKEN is required for --publish" in result.stderr


def test_publish_script_uses_env_token_without_putting_secret_on_command_line(
    tmp_path: Path,
) -> None:
    assert PUBLISH_SCRIPT.exists()
    dist_dir = tmp_path / "dist"
    bin_dir = tmp_path / "bin"
    log_path = tmp_path / "commands.log"
    dist_dir.mkdir()
    bin_dir.mkdir()
    (dist_dir / "eve_client-0.0.0.tar.gz").write_text("sdist", encoding="utf-8")
    (dist_dir / "eve_client-0.0.0-py3-none-any.whl").write_text("wheel", encoding="utf-8")
    (bin_dir / "uv").write_text(
        "#!/usr/bin/env bash\n"
        "echo uv \"$@\" >> \"$EVE_TEST_COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    (bin_dir / "uvx").write_text(
        "#!/usr/bin/env bash\n"
        "echo uvx \"$@\" >> \"$EVE_TEST_COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    (bin_dir / "uv").chmod(0o755)
    (bin_dir / "uvx").chmod(0o755)

    subprocess.run(
        [
            "bash",
            str(PUBLISH_SCRIPT),
            "--publish",
            "--skip-build",
            "--dist-dir",
            str(dist_dir),
        ],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "EVE_TEST_COMMAND_LOG": str(log_path),
            "PYPI_API_TOKEN": "secret-token",
        },
    )

    command_log = log_path.read_text(encoding="utf-8")
    assert "uv publish" in command_log
    assert "--token" not in command_log
    assert "secret-token" not in command_log


def test_publish_workflow_dry_runs_on_pr_and_publishes_only_on_release_tag() -> None:
    assert PUBLISH_WORKFLOW.exists()
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    dry_run_job = workflow.split("  publish:", 1)[0]
    publish_job = "  publish:" + workflow.split("  publish:", 1)[1]

    assert "pull_request:" in workflow
    assert "eve-client@*" in workflow
    assert "workflow_dispatch:" not in workflow
    assert "if: github.event_name == 'pull_request'" in dry_run_job
    assert "packages/client/scripts/publish-eve-client-pypi.sh --dry-run" in dry_run_job
    assert "--publish" not in dry_run_job
    assert "PYPI_API_TOKEN" not in dry_run_job

    assert "if: startsWith(github.ref, 'refs/tags/eve-client@')" in publish_job
    assert "packages/client/scripts/publish-eve-client-pypi.sh --publish" in publish_job
    assert "--dry-run" not in publish_job
    assert "PYPI_API_TOKEN" in publish_job
