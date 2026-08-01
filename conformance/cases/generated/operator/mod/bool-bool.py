# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# 0
# int
a = True
b = True
try:
    r = a % b
    print(r)
    print(type(r).__name__)
except TypeError:
    print('TypeError')
except ZeroDivisionError:
    print('ZeroDivisionError')
