# tier: spec
# ref: reference/compound_stmts.html#type-params
# min-python: 3.12
# expect:
# Alias
# 1
# x
# TypeAliasType
type Alias = list[int]

def first[T](xs: list[T]) -> T:
    return xs[0]

class Box[T]:
    def __init__(self, v: T):
        self.v = v

print(Alias.__name__)
print(first([1, 2]))
print(Box("x").v)
print(type(Alias).__name__)
