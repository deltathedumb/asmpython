# tier: spec
# ref: library/stdtypes.html#text-sequence-type-str
# expect:
# TypeError
# abcd abc
# False
s = "abc"
try:
    s[0] = "z"
except TypeError:
    print("TypeError")
t = s
s += "d"
print(s, t)
print(s is t)
