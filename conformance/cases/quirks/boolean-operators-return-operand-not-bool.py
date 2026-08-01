# tier: spec
# ref: reference/expressions.html#boolean-operations
# expect:
# 'fallback'
# [1]
# None
# 'b'
# int
# None
print(repr([] or "fallback"))
print(repr([1] or "fallback"))
print(repr(None and "never"))
print(repr("a" and "b"))
print(type(1 and 2).__name__)
print(repr(0 or 0.0 or "" or None))
