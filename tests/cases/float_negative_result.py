# expect:
# -1.5
def diff(a, b):
    return a - b
print(diff(3.5, 5.0))
# asmpython (beta/3.14.0) MISMATCH: prints '-2.651066339e-314\n' (wrong).
