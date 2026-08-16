# expect:
# 1.5
x = 5
if x > 3:
    y = 1.5
else:
    y = 2
print(y)
# asmpython (beta/3.14.0) MISMATCH: prints '3\n' (wrong).
