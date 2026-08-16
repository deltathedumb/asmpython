# expect:
# True
a, b, c = True, False, True
print((a and b) or (c and not b))
# asmpython (beta/3.14.0) MISMATCH: prints '1\n' (wrong).
