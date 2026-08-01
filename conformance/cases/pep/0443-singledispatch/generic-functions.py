# tier: spec
# ref: library/functools.html#functools.singledispatch
# expect:
# int str object
# int
import functools

@functools.singledispatch
def describe(v):
    return "object"

@describe.register
def _(v: int):
    return "int"

@describe.register(str)
def _(v):
    return "str"

print(describe(1), describe("a"), describe(1.5))
print(describe(True))
