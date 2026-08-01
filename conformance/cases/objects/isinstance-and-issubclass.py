# tier: spec
# ref: library/functions.html#isinstance
# expect:
# True False
# True False
# True False
# True
class A:
    pass

class B(A):
    pass

print(isinstance(B(), A), isinstance(A(), B))
print(issubclass(B, A), issubclass(A, B))
print(isinstance(True, int), isinstance(1, bool))
print(isinstance(1, (str, int)))
