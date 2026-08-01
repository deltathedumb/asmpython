# tier: spec
# ref: library/stdtypes.html#str.split
# expect:
# ['a', 'b', '', 'c']
# ['a', 'b']
# ['', '', 'a', '', 'b', '', '']
# a-b
# ['']
# 0
print("a,b,,c".split(","))
print("  a  b  ".split())
print("  a  b  ".split(" "))
print("-".join(["a", "b"]))
print("".split(","))
print(len("".split()))
