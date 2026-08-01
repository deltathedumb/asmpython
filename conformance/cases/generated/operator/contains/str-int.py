# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# TypeError
a = 'ab'
b = 7
try:
    r = a in b
    print(r)
    print(type(r).__name__)
except TypeError:
    print('TypeError')
except ZeroDivisionError:
    print('ZeroDivisionError')
