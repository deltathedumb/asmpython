"""atexit module: register functions to be called at program exit.

In asmpython, registered callbacks are stored but not automatically
called (no atexit hook in the native runtime). Call _run_exitfuncs()
explicitly if needed.
"""
from __future__ import annotations


_funcs: list = []
_args_list: list = []


def register(func: int, arg0: int = 0, arg1: int = 0) -> int:
    """Register func to be called at exit. Returns func."""
    _funcs.append(func)
    _args_list.append([arg0, arg1])
    return func


def unregister(func: int) -> None:
    """Remove func from the list of exit handlers."""
    new_funcs: list = []
    new_args: list = []
    i: int = 0
    while i < len(_funcs):
        if _funcs[i] != func:
            new_funcs.append(_funcs[i])
            new_args.append(_args_list[i])
        i = i + 1
    _funcs.clear()
    for f in new_funcs:
        _funcs.append(f)
    _args_list.clear()
    for a in new_args:
        _args_list.append(a)


def _clear() -> None:
    """Clear all registered exit handlers."""
    _funcs.clear()
    _args_list.clear()


def _run_exitfuncs() -> None:
    """Call all registered exit handlers in reverse order."""
    i: int = len(_funcs) - 1
    while i >= 0:
        f: int = _funcs[i]
        args: list = _args_list[i]
        f(args[0], args[1])
        i = i - 1


def _ncallbacks() -> int:
    """Return number of registered callbacks."""
    return len(_funcs)
