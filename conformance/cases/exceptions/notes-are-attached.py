# tier: spec
# ref: library/stdtypes.html#exception-notes
# expect:
# ['first', 'second']
e = ValueError("base")
e.add_note("first")
e.add_note("second")
print(e.__notes__)
