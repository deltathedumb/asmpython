# tier: spec
# ref: reference/simple_stmts.html#the-type-statement
# min-python: 3.12
# expect:
# Alias
# list[int]
# Generic
type Alias = list[int]
type Generic[T] = dict[str, T]
print(Alias.__name__)
print(Alias.__value__)
print(Generic.__name__)
