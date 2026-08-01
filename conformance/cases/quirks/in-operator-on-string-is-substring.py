# tier: spec
# ref: library/stdtypes.html#text-sequence-type-str
# expect:
# True
# True
# True
# True
# False
print("ab" in "xaby")
print("" in "abc")
print("a" in "abc")
print(["a"] == list("a"))
print("ba" in "abc")
