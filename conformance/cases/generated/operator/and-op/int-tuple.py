# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# (7, 2)
# tuple
a = 7
b = (7, 2)
try:
    r = a and b
    print(r)
    print(type(r).__name__)
except TypeError:
    print('TypeError')
except ZeroDivisionError:
    print('ZeroDivisionError')
