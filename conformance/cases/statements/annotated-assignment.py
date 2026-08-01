# tier: spec
# ref: reference/simple_stmts.html#annotated-assignment-statements
# expect:
# 1
# NameError
# ['a', 'b']
# 1
# ['p', 'q', 'return']
# <class 'int'>
x: int = 1
y: str
print(x)
try:
    print(y)
except NameError:
    print("NameError")


class C:
    a: int = 1
    b: str


print(sorted(C.__annotations__))
print(C.a)


def f(p: int, q: "str" = "d") -> bool:
    return True


print(sorted(f.__annotations__))
print(f.__annotations__["p"])
