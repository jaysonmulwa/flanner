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
    "ipc": set(),
    "identity": set(),
    # The provider seam: mesh is pure protocol, and only an adapter may
    # know a vendor. No core module may import an adapter (PRD §10.1).
    "mesh": set(),
    "mesh_fake": {"exceptions", "mesh"},
    # The device half of the mesh seam. An adapter, so it may know a
    # vendor; nothing else may import it.
    "mesh_netbird": {"exceptions", "mesh"},
    # The portability suite. Written against the protocol only, so it
    # cannot accidentally encode how one vendor happens to behave.
    "mesh_conformance": {"exceptions", "mesh"},
    "artifacts": {"identity"},
    "entitlements": {"identity", "artifacts"},
    "device_auth": {"identity", "artifacts"},
    # The entitlement cache. No network here on purpose: read commands
    # resolve authorization through it, and an import boundary is a better
    # guarantee than a promise that nobody will call out.
    "session": {"identity", "entitlements"},
    # The only module below the composition roots that may reach the network.
    "account": {"identity", "device_auth", "entitlements", "session"},
    "authz": {"workflow", "session", "entitlements", "database", "plan_ops"},
    # Peer transport. Talks to other devices, never to the control plane,
    # so it may not import account any more than a read command may.
    "peer": {"entitlements", "identity", "sync", "device_auth"},
    # The transport carries what peer decides; it never decides anything
    # itself, so it reaches for peer and the device key and nothing else.
    "peer_iroh": {"identity", "peer"},
    "workflow": {"artifacts"},
    "assurance": FOUNDATION
    | {"artifacts", "identity", "workflow", "database", "freshness", "authz"},
    "review": FOUNDATION | {"workflow", "assurance", "database", "plan_ops", "authz"},
    "sync": FOUNDATION | {"artifacts", "database"},
    "reconcile": FOUNDATION | {"database", "artifacts", "identity"},
    "services": FOUNDATION
    | {"database", "storage", "plan_ops", "linear_api", "agent_hooks", "ipc", "review"},
    "claude_integration": set(),
    "linear_api": {"exceptions", "linear_utils"},
    "server": FOUNDATION
    | {"database", "storage", "freshness", "services", "artifacts", "assurance", "review"},
    "web": FOUNDATION | {"database", "storage", "plan_ops", "ipc", "services"},
    "agent_hooks": FOUNDATION | {"database"},
    "plan_ops": FOUNDATION | {"database", "storage", "artifacts"},
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
        "ipc",
        "reconcile",
        "services",
        "review",
        "session",
        "account",
        "peer",
        # The composition root chooses a mesh implementation, so it
        # names the adapter and the protocol it is typed against.
        "mesh",
        "mesh_netbird",
        # Choosing between transports means naming both of them.
        "peer_iroh",
        "authz",
        "entitlements",
        "identity",
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
            if node.level > 0 and node.module is None:  # from . import x, y
                # Relative with no module: a name here is either a
                # submodule or a symbol from __init__ (e.g. __version__).
                # Only the former is a boundary crossing. This whole form was
                # previously invisible, so boundaries could be crossed
                # without the test noticing.
                for alias in node.names:
                    if (PACKAGE / f"{alias.name}.py").exists():
                        found.add(alias.name)
            elif node.level > 0 and node.module:  # from .x import y
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


def test_no_read_path_can_reach_the_network():
    """A read command must never make an HTTP call.

    `account` is the one module below the composition roots allowed to
    reach out. Anything that resolves authorization for a read - authz,
    assurance, review - must stay clear of it, transitively. Stated as a
    reachability check rather than a comment, because the tempting shortcut
    when wiring entitlements in is exactly one import away.
    """
    reachable = {}
    for module in ALLOWED:
        path = PACKAGE / f"{module}.py"
        reachable[module] = internal_imports(path) if path.exists() else set()

    def closure(start: str) -> set[str]:
        seen, pending = set(), [start]
        while pending:
            current = pending.pop()
            for dependency in reachable.get(current, set()):
                if dependency not in seen:
                    seen.add(dependency)
                    pending.append(dependency)
        return seen

    for module in ("authz", "assurance", "review", "session", "workflow", "peer"):
        assert "account" not in closure(module), (
            f"{module} can reach the network through account; "
            "a read command would make an HTTP call"
        )
