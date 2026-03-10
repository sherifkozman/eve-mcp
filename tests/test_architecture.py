from __future__ import annotations

import ast
from pathlib import Path

INTEGRATIONS_DIR = Path(__file__).resolve().parents[1] / "eve_client" / "integrations"
DISALLOWED_IMPORT_PREFIXES = (
    "eve_client.apply",
    "eve_client.auth.local_store",
    "eve_client.cli",
)


def _import_targets(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_integration_modules_do_not_import_client_runtime_concretes() -> None:
    for path in INTEGRATIONS_DIR.glob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = _import_targets(tree)
        for target in imports:
            assert not target.startswith(DISALLOWED_IMPORT_PREFIXES), (
                f"{path.name} imports disallowed runtime concrete: {target}"
            )
