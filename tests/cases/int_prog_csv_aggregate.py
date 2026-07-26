# expect:
# [('a', 15), ('b', 20)]
rows = [['a', '10'], ['b', '20'], ['a', '5']]
totals = {}
for name, val in rows:
    totals[name] = totals.get(name, 0) + int(val)
print(sorted(totals.items()))
# asmpython (beta/3.14.0) MISMATCH: prints "[('a', 10737483788), ('b', 5368741895)]\n" (wrong).
