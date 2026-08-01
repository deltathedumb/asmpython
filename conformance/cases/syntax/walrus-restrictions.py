# tier: spec
# ref: reference/expressions.html#assignment-expressions
# expect:
# accepted | (x := 1)
# SyntaxError | x := 1
# accepted | [y := 1]
# SyntaxError | (a.b := 1)
# SyntaxError | ((a, b) := (1, 2))
# accepted | f(x := 1)
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    except ValueError:
        return "ValueError"
    return "accepted"


for src in (
    "(x := 1)",
    "x := 1",
    "[y := 1]",
    "(a.b := 1)",
    "((a, b) := (1, 2))",
    "f(x := 1)",
):
    print(check(src), "|", src)
