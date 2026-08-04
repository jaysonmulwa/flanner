"""Import-boundary enforcement.

Layering contract (see ARCHITECTURE section in README):
- foundation modules import nothing else from the package
- database/storage sit on the foundation only
- server, web, and cli are composition roots; they must not import each other
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "flanner"

FOUNDATION = {
    "exceptions",
    "utils",
    "frontmatter",
    "git_integration",
    "jira_utils",
    "linear_utils",
}
ALLOWED = {
    **{m: set() for m in FOUNDATION},
    "database": {"exceptions"},
    "storage": {"exceptions", "frontmatter", "utils"},
    "freshness": {"utils"},
    "claude_integration": set(),
    "linear_api": {"exceptions", "linear_utils"},
    "server": FOUNDATION | {"database", "storage", "plan_ops", "linear_api", "agent_hooks", "freshness"},
    "web": FOUNDATION | {"database", "storage", "plan_ops"},
    "agent_hooks": FOUNDATION | {"database"},
    "plan_ops": FOUNDATION | {"database", "storage"},
    "cli": FOUNDATION
    | {
        "database",
        "storage",
        "server",
        "web",
        "claude_integration",
        "agent_hooks",
        "linear_api",
        "freshness",
    },
    "__main__": {"cli"},
    "__init__": set(),
}


def internal_imports(path: Path) -> set[str]:
    """All flanner-internal modules imported anywhere in the file (incl. inside functions)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level > 0 and node.module:  # from .x import y
                found.add(node.module.split(".")[0])
            elif node.module and node.module.startswith("flanner."):
                found.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("flanner."):
                    found.add(alias.name.split(".")[1])
    return found


def test_every_module_has_a_boundary_rule():
    modules = {p.stem for p in PACKAGE.glob("*.py")}
    assert modules <= set(ALLOWED), f"add boundary rules for: {modules - set(ALLOWED)}"


def test_import_boundaries_hold():
    violations = []
    for path in PACKAGE.glob("*.py"):
        illegal = internal_imports(path) - ALLOWED[path.stem]
        if illegal:
            violations.append(f"{path.name} imports {sorted(illegal)}")
    assert not violations, "; ".join(violations)
