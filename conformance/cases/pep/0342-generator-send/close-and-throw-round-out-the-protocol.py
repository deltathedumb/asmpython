# tier: spec
# ref: reference/expressions.html#generator-iterator-methods
# expect:
# ready
# ready
# stopped
# True True True
def gen():
    try:
        while True:
            v = yield "ready"
            if v == "stop":
                return "stopped"
    finally:
        pass

g = gen()
print(next(g))
print(g.send("go"))
try:
    g.send("stop")
except StopIteration as e:
    print(e.value)
print(hasattr(g, "close"), hasattr(g, "throw"), hasattr(g, "send"))
