# tier: spec
# ref: library/functions.html#print
# expect:
# a b c
# a-b
# no-newline|
# 1,2,3
# 1 None True
print("a", "b", "c")
print("a", "b", sep="-")
print("no-newline", end="|")
print()
print(*[1, 2, 3], sep=",")
print(1, None, True)
