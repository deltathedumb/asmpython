# probes: for/else is skipped after a break
# expect:
# 1
# after
for v in [1, 2, 3]:
    if v == 2:
        break
    print(v)
else:
    print("exhausted")
print("after")
