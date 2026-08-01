# tier: impl
# ref: reference/expressions.html#displays-for-lists-sets-and-dictionaries
# expect:
# outer
# [5, 5]
import sys

def outer():
    return [sys._getframe().f_code.co_name for _ in range(1)][0]

print(outer())

def uses_closure():
    n = 5
    return [n for _ in range(2)]

print(uses_closure())
