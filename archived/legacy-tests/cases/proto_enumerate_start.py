# probes: enumerate accepts a start index
# expect:
# 1 a
# 2 b
for index, value in enumerate(["a", "b"], start=1):
    print(index, value)
