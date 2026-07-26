# expect:
#     3.14
w = 8
val = 3.14159
print(f'{val:>{w}.2f}')
# asmpython (beta/3.14.0) MISMATCH: prints '{w}.2f\n' (wrong).
