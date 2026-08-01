# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# TypeError
a = 'ab'
b = 'ab'
try:
    r = a // b
    print(r)
    print(type(r).__name__)
except TypeError:
    print('TypeError')
except ZeroDivisionError:
    print('ZeroDivisionError')
