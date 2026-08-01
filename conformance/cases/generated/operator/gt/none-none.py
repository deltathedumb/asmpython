# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# TypeError
a = None
b = None
try:
    r = a > b
    print(r)
    print(type(r).__name__)
except TypeError:
    print('TypeError')
except ZeroDivisionError:
    print('ZeroDivisionError')
