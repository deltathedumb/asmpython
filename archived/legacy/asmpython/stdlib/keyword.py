"""keyword module: Python keyword and soft-keyword lists."""
from __future__ import annotations


kwlist: list[str] = [
    "False",
    "None",
    "True",
    "and",
    "as",
    "assert",
    "async",
    "await",
    "break",
    "class",
    "continue",
    "def",
    "del",
    "elif",
    "else",
    "except",
    "finally",
    "for",
    "from",
    "global",
    "if",
    "import",
    "in",
    "is",
    "lambda",
    "nonlocal",
    "not",
    "or",
    "pass",
    "raise",
    "return",
    "try",
    "while",
    "with",
    "yield",
]

softkwlist: list[str] = [
    "match",
    "case",
    "type",
]


def iskeyword(s: str) -> int:
    """Return True if s is a Python keyword."""
    for kw in kwlist:
        if kw == s:
            return 1
    return 0


def issoftkeyword(s: str) -> int:
    """Return True if s is a Python soft keyword."""
    for kw in softkwlist:
        if kw == s:
            return 1
    return 0


def iskeywordorbuiltin(s: str) -> int:
    """Return True if s is a keyword or builtin."""
    builtins_list: list[str] = [
        "abs", "all", "any", "ascii", "bin", "bool", "breakpoint",
        "bytearray", "bytes", "callable", "chr", "classmethod", "compile",
        "complex", "copyright", "credits", "delattr", "dict", "dir",
        "divmod", "enumerate", "eval", "exec", "exit", "filter", "float",
        "format", "frozenset", "getattr", "globals", "hasattr", "hash",
        "help", "hex", "id", "input", "int", "isinstance", "issubclass",
        "iter", "len", "license", "list", "locals", "map", "max",
        "memoryview", "min", "next", "object", "oct", "open", "ord",
        "pow", "print", "property", "quit", "range", "repr", "reversed",
        "round", "set", "setattr", "slice", "sorted", "staticmethod",
        "str", "sum", "super", "tuple", "type", "vars", "zip",
    ]
    if iskeyword(s):
        return 1
    for b in builtins_list:
        if b == s:
            return 1
    return 0
