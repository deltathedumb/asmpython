# tier: spec
# ref: library/exceptions.html#exception-groups
# expect:
# ExceptionGroup
# 2
# ['TypeError', 'ValueError']
eg = ExceptionGroup("g", [ValueError("v"), TypeError("t")])
print(type(eg).__name__)
print(len(eg.exceptions))
print(sorted(type(x).__name__ for x in eg.exceptions))
