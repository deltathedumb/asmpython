# probes: a list of tuples destructures in the for target
# expect:
# 1 a
# 2 b
rows = [(1, "a"), (2, "b")]
for number, label in rows:
    print(number, label)
