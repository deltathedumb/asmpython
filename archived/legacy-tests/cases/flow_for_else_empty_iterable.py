# probes: for/else runs even for an empty iterable
# expect:
# else ran
for v in []:
    print(v)
else:
    print("else ran")
