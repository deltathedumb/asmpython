# expect:
#     x|
print('{:>{width}}'.format('x', width=5) + '|')
# asmpython (beta/3.14.0) MISMATCH: prints 'x}|\n' (wrong).
