# expect:
# 1111_1111
print(f'{255:_b}')
# asmpython (beta/3.14.0) MISMATCH: prints '11111111\n' (wrong).
