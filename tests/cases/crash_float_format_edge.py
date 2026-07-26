# expect:
# 1,234.57 1.234568e+03 1234.57
x = 1234.5678
print(f'{x:,.2f}', f'{x:e}', f'{x:g}')
# asmpython (beta/3.14.0) MISMATCH: prints '1,234.57 1.234568e+003 1234.57\n' (wrong).
