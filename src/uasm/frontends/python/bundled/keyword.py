"""Keywords of the Python language, as CPython 3.14 lists them.

COVERAGE: complete. `kwlist`, `softkwlist`, `iskeyword`, `issoftkeyword` --
which is the module's entire public surface (`dir(keyword)` minus dunders).

The lists are CPython's own, in CPython's order, because both are observable:
`keyword.kwlist` is a list a program may print or slice, and a set would
answer membership correctly while getting `kwlist[0]` wrong.

CPython generates this file from its grammar. This one is written out, and
`tests/stdlib/keyword.py` compares both lists against the running CPython
element by element -- so a keyword added in a later version fails here rather
than being silently absent.
"""

kwlist = [
    'False',
    'None',
    'True',
    'and',
    'as',
    'assert',
    'async',
    'await',
    'break',
    'class',
    'continue',
    'def',
    'del',
    'elif',
    'else',
    'except',
    'finally',
    'for',
    'from',
    'global',
    'if',
    'import',
    'in',
    'is',
    'lambda',
    'nonlocal',
    'not',
    'or',
    'pass',
    'raise',
    'return',
    'try',
    'while',
    'with',
    'yield',
]

#: SOFT keywords are keywords only where the grammar expects one: `match` is a
#: keyword in `match x:` and an ordinary name in `match = 1`, and both are
#: legal in the same file. `iskeyword` answers False for every one of them,
#: which is the distinction this second list exists to record.
softkwlist = [
    '_',
    'case',
    'match',
    'type',
]

_kwset = frozenset(kwlist)
_softkwset = frozenset(softkwlist)


def iskeyword(s):
    """Whether `s` is a reserved word -- one that can never be a name."""
    return s in _kwset


def issoftkeyword(s):
    """Whether `s` is a keyword only in the positions the grammar says.

    NOT a subset of `iskeyword`: the two lists are disjoint, and a soft
    keyword is a name everywhere the grammar is not expecting the construct.
    """
    return s in _softkwset
