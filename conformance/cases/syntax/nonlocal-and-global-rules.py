# tier: spec
# ref: reference/simple_stmts.html#the-nonlocal-statement
# expect:
# SyntaxError | nonlocal x
# SyntaxError | def f():\n    nonlocal x
# accepted | def f():\n    x = 1\n    def g():\n        nonlocal x\n        x = 2
# accepted | def f():\n    global x\n    x = 1
# SyntaxError | def f():\n    x = 1\n    global x
def check(src):
    try:
        compile(src, "<case>", "exec")
    except SyntaxError:
        return "SyntaxError"
    except ValueError:
        return "ValueError"
    return "accepted"


for src in (
    "nonlocal x",
    "def f():\n    nonlocal x",
    "def f():\n    x = 1\n    def g():\n        nonlocal x\n        x = 2",
    "def f():\n    global x\n    x = 1",
    "def f():\n    x = 1\n    global x",
):
    print(check(src), "|", src.replace("\n", "\\n"))
