# expect:
# 121.00000000000001
def compound(p, r, n):
    return p * (1 + r) ** n
print(compound(100, 0.1, 2))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
