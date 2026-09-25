"""Architecture contracts for runtime and feature dependency direction."""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULES_ROOT = PROJECT_ROOT / "src" / "app" / "modules"


def _imports_api_dependency(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            if module_name == "src.app.api" or module_name.startswith("src.app.api."):
                violations.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(alias.name == "src.app.api" or alias.name.startswith("src.app.api.") for alias in node.names):
                violations.append(node.lineno)
    return violations


def _is_request_adapter(path: Path) -> bool:
    relative_path = path.relative_to(MODULES_ROOT)
    return (
        relative_path.name == "router.py"
        or relative_path.parent.name == "routers"
        or relative_path == Path("ai/middleware.py")
    )


def test_feature_implementation_does_not_import_api_dependencies() -> None:
    violations: list[str] = []
    for path in MODULES_ROOT.rglob("*.py"):
        if _is_request_adapter(path):
            continue
        for line_number in _imports_api_dependency(path):
            violations.append(f"{path.relative_to(PROJECT_ROOT)}:{line_number}")

    assert not violations, "Feature implementation imports API dependencies:\n" + "\n".join(violations)


def test_core_setup_contains_no_application_composition_imports() -> None:
    setup_source = (PROJECT_ROOT / "src" / "app" / "core" / "setup.py").read_text(encoding="utf-8")
    assert "src.app.api" not in setup_source
    assert "src.app.modules" not in setup_source
    assert "src.app.admin" not in setup_source


def test_api_ownership_helpers_re_export_core_policy() -> None:
    from src.app.api.deps import request_context
    from src.app.core.security import ownership

    assert request_context.owns_resource is ownership.owns_resource
    assert request_context.require_resource_ownership is ownership.require_resource_ownership
