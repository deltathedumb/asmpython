# tier: spec
# ref: library/exceptions.html#exception-groups
# min-python: 3.11
# expect:
# ExceptionGroup 2
# ExceptionGroup 1
# ['ExceptionGroup', 'ValueError']
# True
eg = ExceptionGroup("outer", [
    ValueError("v1"),
    ExceptionGroup("inner", [TypeError("t1")]),
])
print(type(eg).__name__, len(eg.exceptions))
match, rest = eg.split(ValueError)
print(type(match).__name__, len(match.exceptions))
print(sorted(type(e).__name__ for e in eg.exceptions))
sub = eg.subgroup(TypeError)
print(sub is not None)
