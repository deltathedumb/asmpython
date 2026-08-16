# expect:
# alice
# bob
# carol
# 30
# 25
# 28
# sum: 83
# count: 3

# Dicts are insertion-ordered (CPython 3.7+ guarantee).
d = {"alice": 30, "bob": 25, "carol": 28}

for k in d.keys():
    print(k)

for v in d.values():
    print(v)

# Use the materialized lists.
total = 0
for v in d.values():
    total = total + v
print("sum:", total)

# len() on the materialized list.
print("count:", len(d.keys()))
