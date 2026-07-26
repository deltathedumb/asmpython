# expect:
# 1.234e-05 1.234e+06
print(f'{0.00001234:g}', f'{1234000.0:g}')
# asmpython (beta/3.14.0) MISMATCH: prints '1.234e-005 1.234e+006\n' (wrong).
