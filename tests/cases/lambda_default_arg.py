# expect:
# 15 25
f = lambda x, y=10: x + y
print(f(5), f(5, 20))
# asmpython (beta/3.14.0) MISMATCH: prints '5368746583 25\n' (wrong).
