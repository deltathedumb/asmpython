# expect:
# carol
# bob
# alice
# 28
# 25
# 30
# sum: 83
# count: 3

# Iteration order matches the hashtable's slot order (FNV-1a buckets), not
# insertion order. We just freeze whatever order the runtime produces.
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
