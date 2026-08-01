# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# ab
# str
a = 7
b = 'ab'
try:
    r = a and b
    print(r)
    print(type(r).__name__)
except TypeError:
    print('TypeError')
except ZeroDivisionError:
    print('ZeroDivisionError')
