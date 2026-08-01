# tier: spec
# ref: library/stdtypes.html#str.split
# expect:
# ['a', 'b', 'c']
# ['a', 'b', '', 'c']
# ['a', 'b,c']
# ['a,b', 'c']
# ['a', 'c']
# ['', 'a', '']
# a-bXc
print("a b  c".split())
print("a b  c".split(" "))
print("a,b,c".split(",", 1))
print("a,b,c".rsplit(",", 1))
print("abc".split("b"))
print(",a,".split(","))
print("aXbXc".replace("X", "-", 1))
