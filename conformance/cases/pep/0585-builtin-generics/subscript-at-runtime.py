# tier: spec
# ref: peps.python.org/pep-0585/
# expect:
# list[int]
# True
# dict[str, int]
# 2
t = list[int]
print(t)
print(t.__origin__ is list)
print(dict[str, int])
def f(xs: list[int]) -> int:
    return len(xs)

print(f([1, 2]))
