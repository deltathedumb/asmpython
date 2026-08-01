# tier: cpython
# expect:
# division by zero
# list index out of range
try:
    1 / 0
except ZeroDivisionError as e:
    print(str(e))
try:
    [1, 2][9]
except IndexError as e:
    print(str(e))
