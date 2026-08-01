# tier: spec
# ref: library/stdtypes.html#str.partition
# expect:
# ('a', '=', 'b=c')
# ('a=b', '=', 'c')
# ('a', '', '')
# ['x', 'y']
# ['x\n', 'y\n']
print("a=b=c".partition("="))
print("a=b=c".rpartition("="))
print("a".partition("="))
print("x\ny\n".splitlines())
print("x\ny\n".splitlines(True))
