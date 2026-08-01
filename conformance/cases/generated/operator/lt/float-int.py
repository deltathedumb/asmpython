# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# True
# bool
a = 2.5
b = 7
try:
    r = a < b
    print(r)
    print(type(r).__name__)
except TypeError:
    print('TypeError')
except ZeroDivisionError:
    print('ZeroDivisionError')
