# tier: spec
# ref: reference/expressions.html#boolean-operations
# expect:
# fallback
# first
# 2
# 0
# int
print(0 or "fallback")
print("first" or "second")
print(1 and 2)
print(0 and 2)
print(type(1 and 2).__name__)
