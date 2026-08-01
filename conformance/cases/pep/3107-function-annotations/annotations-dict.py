# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# ['a', 'b', 'return']
# <class 'int'>
# True
def f(a: int, b: "str" = "x") -> bool:
    return True

ann = f.__annotations__
print(sorted(ann))
print(ann["a"])
print(f(1))
