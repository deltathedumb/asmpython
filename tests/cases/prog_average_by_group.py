# expect:
# [('a', 20.0), ('b', 30.0)]
data = [('a', 10), ('b', 20), ('a', 30), ('b', 40)]
groups = {}
for key, val in data:
    groups.setdefault(key, []).append(val)
averages = {k: sum(v) / len(v) for k, v in groups.items()}
print(sorted(averages.items()))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'append'
