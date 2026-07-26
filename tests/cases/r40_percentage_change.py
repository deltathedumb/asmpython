# expect:
# 50.0
def pct_change(old, new):
    return (new - old) / old * 100
print(round(pct_change(100, 150), 1))
# asmpython (beta/3.14.0) MISMATCH: prints '100.0\n' (wrong).
