# expect:
# False True
n = float('nan')
print(n == n, n != n)
# asmpython (beta/3.14.0) MISMATCH: prints 'True False\n' (wrong).
