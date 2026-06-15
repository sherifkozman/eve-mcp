from __future__ import annotations

import os
import subprocess
import tarfile
import tomllib
import venv
import zipfile
from pathlib import Path

def _find_package_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists() and (parent / "eve_client").is_dir():
            return parent
    raise AssertionError("Could not locate eve-memory-client package root")


PACKAGE_ROOT = _find_package_root()
MONOREPO_ROOT = PACKAGE_ROOT.parent.parent
if (MONOREPO_ROOT / "packages" / "client" / "pyproject.toml").exists():
    REPO_ROOT = MONOREPO_ROOT
else:
    REPO_ROOT = PACKAGE_ROOT
INSTALL_SCRIPT = PACKAGE_ROOT / "scripts" / "install-eve-client.sh"
STANDALONE_INSTALL_SCRIPT = PACKAGE_ROOT / "install.sh"
PUBLISH_SCRIPT = PACKAGE_ROOT / "scripts" / "publish-eve-client-pypi.sh"
PUBLISH_WORKFLOW = PACKAGE_ROOT / ".github" / "workflows" / "release-eve-client.yml"
PYPI_DISTRIBUTION = "eve-memory-client"
DIST_FILE_PREFIX = "eve_memory_client"


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


def _package_version() -> str:
    data = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _write_fake_uv(bin_dir: Path, installed_bin_dir: Path, command_log: Path) -> None:
    (bin_dir / "uv").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [ \"$1\" = \"tool\" ] && [ \"$2\" = \"dir\" ] && [ \"$3\" = \"--bin\" ]; then\n"
        "  printf '%s\\n' \"$EVE_FAKE_UV_BIN_DIR\"\n"
        "  exit 0\n"
        "fi\n"
        "echo uv \"$@\" >> \"$EVE_TEST_COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    (bin_dir / "uv").chmod(0o755)
    (installed_bin_dir / "eve").write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = \"version\" ]; then echo \"$EVE_FAKE_EVE_VERSION\"; else echo \"$@\"; fi\n",
        encoding="utf-8",
    )
    (installed_bin_dir / "eve").chmod(0o755)


def test_built_wheel_contains_expected_runtime_files(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    _run("uv", "build", str(PACKAGE_ROOT), "--out-dir", str(dist_dir))
    wheel_path = next(dist_dir.glob(f"{DIST_FILE_PREFIX}-*.whl"))
    package_version = _package_version()

    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())
        dist_info = f"{DIST_FILE_PREFIX}-{package_version}.dist-info"
        entry_points = wheel.read(f"{dist_info}/entry_points.txt").decode("utf-8")
        metadata = wheel.read(f"{dist_info}/METADATA").decode("utf-8")

    assert "eve_client/__main__.py" in names
    assert "eve_client/cli.py" in names
    assert "eve_client/tests/test_cli.py" not in names
    assert f"Name: {PYPI_DISTRIBUTION}" in metadata
    assert "eve = eve_client.cli:main" in entry_points
    assert f"Version: {package_version}" in metadata


def test_built_sdist_contains_readme_and_package_sources(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    _run("uv", "build", str(PACKAGE_ROOT), "--out-dir", str(dist_dir))
    sdist_path = next(dist_dir.glob(f"{DIST_FILE_PREFIX}-*.tar.gz"))

    with tarfile.open(sdist_path, "r:gz") as sdist:
        names = set(sdist.getnames())

    root_prefix = f"{DIST_FILE_PREFIX}-{_package_version()}"
    assert f"{root_prefix}/README.md" in names
    assert f"{root_prefix}/pyproject.toml" in names
    assert f"{root_prefix}/eve_client/__main__.py" in names


def test_installed_wheel_exposes_eve_entrypoint_and_module_entrypoint(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    _run("uv", "build", str(PACKAGE_ROOT), "--out-dir", str(dist_dir))
    wheel_path = next(dist_dir.glob(f"{DIST_FILE_PREFIX}-*.whl"))

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(venv_dir)
    venv_python = venv_dir / "bin" / "python"
    venv_eve = venv_dir / "bin" / "eve"

    _run(str(venv_python), "-m", "pip", "install", str(wheel_path), cwd=tmp_path)

    eve_result = _run(str(venv_eve), "version", cwd=tmp_path)
    module_result = _run(str(venv_python), "-m", "eve_client", "version", cwd=tmp_path)

    expected = _package_version()
    assert eve_result.stdout.strip() == expected
    assert module_result.stdout.strip() == expected


def test_installed_wheel_imports_all_default_console_script_modules_in_isolated_venv(
    tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    _run("uv", "build", str(PACKAGE_ROOT), "--out-dir", str(dist_dir))
    wheel_path = next(dist_dir.glob(f"{DIST_FILE_PREFIX}-*.whl"))

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = venv_dir / "bin" / "python"

    _run(str(venv_python), "-m", "pip", "install", str(wheel_path), cwd=tmp_path)

    result = _run(
        str(venv_python),
        "-c",
        "import eve_client.cli; import eve_client.claude_hooks; "
        "import eve_client.gemini_hooks; import eve_client.server; print('ok')",
        cwd=tmp_path,
    )

    assert result.stdout.strip() == "ok"


def test_installers_use_fixed_pypi_distribution_without_source_or_flag_overrides() -> None:
    for script_path in (INSTALL_SCRIPT, STANDALONE_INSTALL_SCRIPT):
        script = script_path.read_text(encoding="utf-8")

        assert "eve-memory-client" in script
        assert "EVE_CLIENT_SOURCE" not in script
        assert "EVE_CLIENT_INSTALL_FLAGS" not in script
        assert "EVE_CLIENT_BINARY" not in script
        assert "EVE_CLIENT_ALLOW_SHADOWED_BINARY" not in script
        assert "EVE_CLIENT_FAIL_ON_SHADOWED_BINARY" not in script

    standalone_script = STANDALONE_INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "curl -LsSf https://astral.sh/uv/install.sh | sh" not in standalone_script


def test_install_script_installs_fixed_package_and_ignores_source_flag_overrides(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    installed_bin = tmp_path / "installed-bin"
    command_log = tmp_path / "commands.log"
    home.mkdir()
    fake_bin.mkdir()
    installed_bin.mkdir()
    _write_fake_uv(fake_bin, installed_bin, command_log)
    env = {
        "HOME": str(home),
        "EVE_CLIENT_SOURCE": "git+https://attacker.example/eve-client.git",
        "EVE_CLIENT_INSTALL_FLAGS": "--index-url https://attacker.example/simple",
        "EVE_CLIENT_BINARY": "not-eve",
        "EVE_FAKE_EVE_VERSION": _package_version(),
        "EVE_FAKE_UV_BIN_DIR": str(installed_bin),
        "EVE_TEST_COMMAND_LOG": str(command_log),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
    }

    install_result = subprocess.run(
        ["bash", str(INSTALL_SCRIPT)],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=True,
        env={**os.environ, **env},
    )
    eve_binary = installed_bin / "eve"

    assert "Installed executable:" in install_result.stdout
    assert str(eve_binary) in install_result.stdout
    assert "SECURITY WARNING:" not in install_result.stderr
    command_log_text = command_log.read_text(encoding="utf-8")
    assert "uv tool install eve-memory-client" in command_log_text
    assert "attacker.example" not in command_log_text
    assert "--index-url" not in command_log_text


def test_install_script_fails_closed_on_shadowed_binary_in_non_interactive_mode(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    installed_bin = tmp_path / "installed-bin"
    command_log = tmp_path / "commands.log"
    home.mkdir()
    fake_bin.mkdir()
    installed_bin.mkdir()
    _write_fake_uv(fake_bin, installed_bin, command_log)
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
            "EVE_CLIENT_BINARY": "not-eve",
            "EVE_FAKE_EVE_VERSION": _package_version(),
            "EVE_FAKE_UV_BIN_DIR": str(installed_bin),
            "EVE_TEST_COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode != 0
    assert "SECURITY WARNING:" in result.stderr
    assert (
        "Aborting because a conflicting eve binary is ahead of the installed one on PATH."
        in result.stderr
    )
    assert "Update PATH so the installed Eve client comes first" in result.stderr


def test_standalone_install_script_fails_closed_on_shadowed_binary(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    shadow_bin = tmp_path / "shadow-bin"
    installed_bin = tmp_path / "installed-bin"
    command_log = tmp_path / "commands.log"
    home.mkdir()
    fake_bin.mkdir()
    shadow_bin.mkdir()
    installed_bin.mkdir()
    _write_fake_uv(fake_bin, installed_bin, command_log)
    (shadow_bin / "eve").write_text("#!/usr/bin/env bash\necho shadow-eve\n", encoding="utf-8")
    (shadow_bin / "eve").chmod(0o755)

    result = subprocess.run(
        ["bash", str(STANDALONE_INSTALL_SCRIPT)],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HOME": str(home),
            "EVE_CLIENT_BINARY": "not-eve",
            "EVE_FAKE_EVE_VERSION": _package_version(),
            "EVE_FAKE_UV_BIN_DIR": str(installed_bin),
            "EVE_TEST_COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{shadow_bin}:{os.environ['PATH']}",
        },
    )

    assert result.returncode != 0
    assert "SECURITY WARNING:" in result.stderr
    assert "Aborting because a conflicting eve binary is ahead of the installed one on PATH." in result.stderr


def test_standalone_install_script_rejects_piped_execution() -> None:
    result = subprocess.run(
        ["sh"],
        cwd=str(REPO_ROOT),
        text=True,
        input=STANDALONE_INSTALL_SCRIPT.read_text(encoding="utf-8"),
        capture_output=True,
    )

    assert result.returncode != 0
    assert "Do not pipe this installer directly to sh." in result.stderr


def test_publish_script_dry_run_checks_artifacts_without_uploading(tmp_path: Path) -> None:
    assert PUBLISH_SCRIPT.exists()
    dist_dir = tmp_path / "dist"
    bin_dir = tmp_path / "bin"
    log_path = tmp_path / "commands.log"
    dist_dir.mkdir()
    bin_dir.mkdir()
    (dist_dir / "eve_memory_client-0.0.0.tar.gz").write_text("sdist", encoding="utf-8")
    (dist_dir / "eve_memory_client-0.0.0-py3-none-any.whl").write_text("wheel", encoding="utf-8")
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
    (dist_dir / "eve_memory_client-0.0.0.tar.gz").write_text("sdist", encoding="utf-8")
    (dist_dir / "eve_memory_client-0.0.0-py3-none-any.whl").write_text("wheel", encoding="utf-8")
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
    (dist_dir / "eve_memory_client-0.0.0.tar.gz").write_text("sdist", encoding="utf-8")
    (dist_dir / "eve_memory_client-0.0.0-py3-none-any.whl").write_text("wheel", encoding="utf-8")
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
    assert "PYPI_API_TOKEN is required for --publish outside GitHub Actions" in result.stderr


def test_publish_script_uses_trusted_publishing_in_github_actions_without_token(
    tmp_path: Path,
) -> None:
    assert PUBLISH_SCRIPT.exists()
    dist_dir = tmp_path / "dist"
    bin_dir = tmp_path / "bin"
    log_path = tmp_path / "commands.log"
    dist_dir.mkdir()
    bin_dir.mkdir()
    (dist_dir / "eve_memory_client-0.0.0.tar.gz").write_text("sdist", encoding="utf-8")
    (dist_dir / "eve_memory_client-0.0.0-py3-none-any.whl").write_text("wheel", encoding="utf-8")
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
            "GITHUB_ACTIONS": "true",
            "PYPI_API_TOKEN": "",
        },
    )

    command_log = log_path.read_text(encoding="utf-8")
    assert "uv publish --trusted-publishing always" in command_log
    assert "secret-token" not in command_log


def test_publish_script_uses_env_token_without_putting_secret_on_command_line(
    tmp_path: Path,
) -> None:
    assert PUBLISH_SCRIPT.exists()
    dist_dir = tmp_path / "dist"
    bin_dir = tmp_path / "bin"
    log_path = tmp_path / "commands.log"
    dist_dir.mkdir()
    bin_dir.mkdir()
    (dist_dir / "eve_memory_client-0.0.0.tar.gz").write_text("sdist", encoding="utf-8")
    (dist_dir / "eve_memory_client-0.0.0-py3-none-any.whl").write_text("wheel", encoding="utf-8")
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


def test_release_workflow_publishes_from_client_repo_on_release_tag() -> None:
    assert PUBLISH_WORKFLOW.exists()
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    publish_job = "  publish-python:" + workflow.split("  publish-python:", 1)[1]

    assert "eve-memory-client@*" in workflow
    assert "workflow_dispatch:" in workflow
    assert "uv run --with pytest --with pytest-cov pytest" in workflow
    assert "mkdir -p \"$RUNNER_TEMP/pytest-tmp\"" in workflow
    assert "TMPDIR=\"$RUNNER_TEMP/pytest-tmp\"" in workflow

    assert "if: startsWith(github.ref, 'refs/tags/eve-memory-client@')" in publish_job
    assert "bash scripts/publish-eve-client-pypi.sh --publish" in publish_job
    assert "--dry-run" not in publish_job
    assert "id-token: write" in publish_job
    assert "PYPI_API_TOKEN" not in publish_job
