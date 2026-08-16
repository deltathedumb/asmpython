# expect:
# $1234.5
amount = 1234.5
print('$' + str(round(amount, 2)))
# asmpython (beta/3.14.0) MISMATCH: prints '$1.56\n' (wrong).
