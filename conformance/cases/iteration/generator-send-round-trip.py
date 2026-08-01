# tier: spec
# ref: reference/expressions.html#generator.send
# expect:
# ('got', None)
# ('got', 'a')
# ('got', 'b')
# StopIteration done
def echo():
    received = None
    while True:
        received = yield ("got", received)
        if received == "stop":
            return "done"

g = echo()
print(next(g))
print(g.send("a"))
print(g.send("b"))
try:
    g.send("stop")
except StopIteration as e:
    print("StopIteration", e.value)
