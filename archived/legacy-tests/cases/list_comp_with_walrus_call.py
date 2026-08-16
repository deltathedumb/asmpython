# expect:
# [0, 1, 4]
def sq(x):
    return x * x
print([s for x in range(4) if (s := sq(x)) < 5])
# asmpython (beta/3.14.0) MISMATCH: prints '[0, 0, 0, 0]\n' (wrong).
