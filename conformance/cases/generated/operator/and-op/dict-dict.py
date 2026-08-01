# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# {'ab': 1}
# dict
a = {'ab': 1}
b = {'ab': 1}
try:
    r = a and b
    print(r)
    print(type(r).__name__)
except TypeError:
    print('TypeError')
except ZeroDivisionError:
    print('ZeroDivisionError')
