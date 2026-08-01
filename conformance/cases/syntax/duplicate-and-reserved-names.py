# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# SyntaxError | def f(a, a): pass
# SyntaxError | lambda a, a: a
# accepted | class C:\n    pass
# SyntaxError | def None(): pass
# SyntaxError | True = 1
# SyntaxError | import x as True
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    except ValueError:
        return "ValueError"
    return "accepted"


for src in (
    "def f(a, a): pass",
    "lambda a, a: a",
    "class C:\n    pass",
    "def None(): pass",
    "True = 1",
    "import x as True",
):
    print(check(src), "|", src.replace("\n", "\\n"))
