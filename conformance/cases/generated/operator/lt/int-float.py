# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# False
# bool
a = 7
b = 2.5
try:
    r = a < b
    print(r)
    print(type(r).__name__)
except TypeError:
    print('TypeError')
except ZeroDivisionError:
    print('ZeroDivisionError')
