# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# SyntaxError | def f(a=1, b): pass
# SyntaxError | def f(*a, *b): pass
# SyntaxError | def f(**a, b): pass
# accepted | def f(a, *, b): pass
# accepted | def f(a, /, b): pass
# SyntaxError | a, *b, *c = [1, 2, 3]
# SyntaxError | *a = [1]
# accepted | a, *b = [1, 2]
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    except ValueError:
        return "ValueError"
    return "accepted"


for src in (
    "def f(a=1, b): pass",
    "def f(*a, *b): pass",
    "def f(**a, b): pass",
    "def f(a, *, b): pass",
    "def f(a, /, b): pass",
    "a, *b, *c = [1, 2, 3]",
    "*a = [1]",
    "a, *b = [1, 2]",
):
    print(check(src), "|", src)
