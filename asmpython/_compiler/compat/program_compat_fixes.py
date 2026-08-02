"""Compatibility fixes for whole-program project discovery.

These patches remain separate while the whole-program loader is still rapidly
expanding. Each one corrects general Python project behavior and is covered by
focused regressions plus downstream Somnia differential tests.
"""

from __future__ import annotations

from pathlib import Path

from .. import program


def _resolve_absolute_workspace_safe(module: str, root: Path) -> Path | None:
    """Resolve an absolute project import without confusing a repo for a package.

    A common layout is::

        somnia/                 # repository/workspace root
            pyproject.toml
            somnia/             # top-level Python package
                __init__.py

    Both directories share the same name. The original resolver treated the
    workspace root itself as the package whenever ``root.name == parts[0]``,
    skipping ``root / parts[0]`` and therefore missing the package entirely.

    The root can represent the package directly only when it actually contains
    ``__init__.py``. Otherwise it is a workspace and the first dotted component
    must be appended normally.
    """
    parts = module.split(".")
    if not parts:
        return None

    root_is_package = (root / "__init__.py").is_file()
    candidates: list[Path] = []
    if root.name == parts[0] and root_is_package:
        candidates.append(root)
    else:
        candidates.append(root / parts[0])
        # Standard ``src/`` project layout: the workspace marker lives at
        # ``root`` while importable top-level packages live below
        # ``root/src``.
        candidates.append(root / "src" / parts[0])

    for candidate in candidates:
        target = candidate
        for part in parts[1:]:
            target = target / part

        py = Path(str(target) + ".py")
        if py.is_file() and program._within(py, root):
            return py

        init = target / "__init__.py"
        if init.is_file() and program._within(init, root):
            return init
    return None


program._resolve_absolute = _resolve_absolute_workspace_safe
