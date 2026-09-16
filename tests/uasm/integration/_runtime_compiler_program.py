# `compile`, `eval` and `exec` are BUNDLED PYTHON spliced into this program:
# the parser answers whether source is valid, and the walker runs it.
def valid(src):
    try:
        compile(src, "<t>", "exec")
    except IndentationError:
        return "IndentationError"
    except SyntaxError:
        return "SyntaxError"
    return "accepted"


for src in ("break", "x = 1", "def f():\n    return 1", "return 1",
            "def g():\nreturn 1", "def f(a, a): pass", "a, *b = [1, 2]"):
    print(valid(src), "|", src.replace("\n", "\n"))

print(type(compile("x = 1", "<t>", "exec")).__name__)

# `eval` ANSWERS A VALUE, and the exceptions come out as themselves.
print(eval("1 + 2 * 3"))
print(eval("(1, 2)[1]"))
print(eval("len('abcd')"), eval("int('42')"), eval("max(3, 9)"))
for thunk in ("1/0", "[1,2][9]", '{}["k"]', "int('x')", "len(5)"):
    try:
        eval(thunk)
    except Exception as e:
        print(type(e).__name__)

# A namespace the caller supplies.
ns = {"a": 10, "b": 4}
print(eval("a * b + 1", ns))

# `exec` RUNS STATEMENTS and answers None.
scope = {}
print(exec("total = 0\nfor i in (1, 2, 3):\n    total = total + i", scope))
print(scope["total"])
exec("def twice(n):\n    return n * 2", scope)
print(scope["twice"](21))
