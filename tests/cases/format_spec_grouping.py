# expect:
# 1,234,567 1111_1111
print(format(1234567, ','), format(255, '_b'))
# asmpython (beta/3.14.0) MISMATCH: prints '1,234,567 11111111\n' (wrong).
