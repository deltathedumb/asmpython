# tier: spec
# ref: reference/compound_stmts.html#type-params
# min-python: 3.12
# expect:
# a
# [1]
# T
def first[T](xs: list[T]) -> T:
    return xs[0]

class Stack[T]:
    def __init__(self):
        self.items: list[T] = []
    def push(self, v: T) -> None:
        self.items.append(v)

print(first(["a", "b"]))
s = Stack()
s.push(1)
print(s.items)
print(first.__type_params__[0].__name__)
