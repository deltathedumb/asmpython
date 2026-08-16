# expect:
# 1.23e+04
print(f'{12345.678:.2e}')
# asmpython (beta/3.14.0) MISMATCH: prints '1.23e+004\n' (wrong).
