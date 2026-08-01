# tier: spec
# ref: reference/simple_stmts.html#the-break-statement
# expect:
# SyntaxError | break
# SyntaxError | continue
# accepted | for i in []:\n    break
# accepted | while True:\n    continue
# SyntaxError | def f():\n    break
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    except ValueError:
        return "ValueError"
    return "accepted"


for src in (
    "break",
    "continue",
    "for i in []:\n    break",
    "while True:\n    continue",
    "def f():\n    break",
):
    print(check(src), "|", src.replace("\n", "\\n"))
