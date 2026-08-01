# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# 9.882117688026186
# float
a = 2.5
b = 2.5
try:
    r = a ** b
    print(r)
    print(type(r).__name__)
except TypeError:
    print('TypeError')
except ZeroDivisionError:
    print('ZeroDivisionError')
