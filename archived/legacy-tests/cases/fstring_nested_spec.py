# expect:
# 3.14
w = 5
print(f'{3.14159:.{w - 3}f}')
# a nested format spec ({...:.{w}f}) is not evaluated; asmpython prints the literal.
