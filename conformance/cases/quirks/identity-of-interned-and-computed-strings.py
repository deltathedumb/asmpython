# tier: impl
# ref: library/stdtypes.html#text-sequence-type-str
# expect:
# True True
# True
# False True
a = "hello"
b = "hello"
print(a is b, a == b)
c = "hel" + "lo"
print(c is a)
d = "".join(["hel", "lo"])
print(d is a, d == a)
