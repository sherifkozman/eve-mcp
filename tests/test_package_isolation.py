from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "eve_client"
PACKAGE_ROOT = ROOT.parent
FORBIDDEN_PREFIXES = (
    "eve_memory",
    "services.",
    "packages.db",
    "packages/contracts",
)


def test_client_package_does_not_import_managed_runtime_modules() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith(FORBIDDEN_PREFIXES):
                    offenders.append(f"{path.relative_to(ROOT.parent)} -> {name}")
    assert offenders == []


def test_client_package_has_distribution_readme() -> None:
    assert (PACKAGE_ROOT / "README.md").exists()


def test_client_package_has_module_entrypoint() -> None:
    assert (ROOT / "__main__.py").exists()
