# tier: spec
# ref: reference/simple_stmts.html#assignment-statements
# expect:
# SyntaxError | 1 = x
# SyntaxError | f() = 1
# SyntaxError | (a, 1) = (1, 2)
# SyntaxError | x + 1 = 2
# accepted | [a, b] = [1, 2]
# accepted | a = 1
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    except ValueError:
        return "ValueError"
    return "accepted"


for src in (
    "1 = x",
    "f() = 1",
    "(a, 1) = (1, 2)",
    "x + 1 = 2",
    "[a, b] = [1, 2]",
    "a = 1",
):
    print(check(src), "|", src)
