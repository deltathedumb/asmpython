# expect:
# False True
a = [1, 2]
b = [1, 2]
print(a is b, a == b)
# asmpython (beta/3.14.0) MISMATCH: prints 'False False\n' (wrong).
