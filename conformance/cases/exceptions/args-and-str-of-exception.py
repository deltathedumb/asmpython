# tier: spec
# ref: library/exceptions.html#BaseException
# expect:
# ('a', 'b')
# ('a', 'b')
# ValueError('a', 'b')
#
# ('one',)
e = ValueError("a", "b")
print(e.args)
print(str(e))
print(repr(e))
print(str(ValueError()))
print(ValueError("one").args)
