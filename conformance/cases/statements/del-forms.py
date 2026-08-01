# tier: spec
# ref: reference/simple_stmts.html#the-del-statement
# expect:
# [2, 3]
# ['b']
# False
# deleted
xs = [1, 2, 3]
del xs[0]
print(xs)
d = {"a": 1, "b": 2}
del d["a"]
print(sorted(d))
class C:
    pass
c = C()
c.v = 1
del c.v
print(hasattr(c, "v"))
a = b = 1
del a, b
print("deleted")
