# probes: for/else runs when the loop is not broken
# expect:
# 1
# 2
# exhausted
for v in [1, 2]:
    print(v)
else:
    print("exhausted")
