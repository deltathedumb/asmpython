# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# True
# bool
a = True
b = True
try:
    r = a or b
    print(r)
    print(type(r).__name__)
except TypeError:
    print('TypeError')
except ZeroDivisionError:
    print('ZeroDivisionError')
