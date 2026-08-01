# tier: spec
# ref: library/functions.html#type
# expect:
# C
# 1 2
# True
# type
C = type("C", (), {"v": 1, "get": lambda self: self.v * 2})
c = C()
print(C.__name__)
print(c.v, c.get())
print(isinstance(c, C))
print(type(C).__name__)
