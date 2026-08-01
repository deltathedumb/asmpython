# tier: spec
# ref: library/functions.html#locals
# min-python: 3.13
# expect:
# (1, 2)
# 1
# ['f']
def f():
    x = 1
    snapshot = locals()
    x = 2
    return snapshot.get("x"), locals().get("x")

print(f())

def g():
    y = 1
    d = locals()
    d["y"] = 99
    return y

print(g())
print(sorted(k for k in locals() if not k.startswith("_"))[:1])
