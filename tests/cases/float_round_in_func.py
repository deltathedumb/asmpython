# expect:
# 3.14
def round2(x):
    return round(x, 2)
print(round2(3.14159))
# asmpython (beta/3.14.0) MISMATCH: prints '0.0\n' (wrong).
