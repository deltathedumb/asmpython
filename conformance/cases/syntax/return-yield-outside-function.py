# tier: spec
# ref: reference/simple_stmts.html#the-return-statement
# expect:
# SyntaxError | return 1
# SyntaxError | yield 1
# accepted | def f():\n    return 1
# accepted | def f():\n    yield 1
# SyntaxError | await x
# accepted | async def f():\n    await x
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    except ValueError:
        return "ValueError"
    return "accepted"


for src in (
    "return 1",
    "yield 1",
    "def f():\n    return 1",
    "def f():\n    yield 1",
    "await x",
    "async def f():\n    await x",
):
    print(check(src), "|", src.replace("\n", "\\n"))
