# tier: spec
# ref: reference/compound_stmts.html#type-params
# min-python: 3.13
# expect:
# 3
# True
# int
class Box[T = int]:
    def __init__(self, v):
        self.v = v

print(Box(3).v)
print(Box.__type_params__[0].has_default())
print(Box.__type_params__[0].__default__.__name__)
