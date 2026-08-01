# tier: spec
# ref: reference/expressions.html#parenthesized-forms
# expect:
# int
# tuple
# 1
# ()
# tuple
print(type((1)).__name__)
print(type((1,)).__name__)
print(len((1,)))
print(())
print(type(()).__name__)
