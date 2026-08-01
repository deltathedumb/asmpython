# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# SyntaxError | def f(a, (b, c)): pass
# SyntaxError | lambda (a, b): a
# accepted | def f(a, bc): pass
# accepted | def f(pair): a, b = pair; return a
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    return "accepted"


for src in (
    "def f(a, (b, c)): pass",
    "lambda (a, b): a",
    "def f(a, bc): pass",
    "def f(pair): a, b = pair; return a",
):
    print(check(src), "|", src)
