# probe102: complex dict/set interactions, defaultdict-like patterns
# Counter-like dict
def count_items(items: list) -> dict:
    counts: dict = {}
    for item in items:
        if item not in counts:
            counts[item] = 0
        counts[item] = counts[item] + 1
    return counts

words = "the quick brown fox jumps over the lazy dog".split()
counts = count_items(words)
print(counts["the"])   # 2
print(counts["fox"])   # 1

# most common
def most_common(counts: dict) -> str:
    best_key = ""
    best_val: int = -1
    for k in counts:
        if counts[k] > best_val:
            best_val = counts[k]
            best_key = k
    return best_key

print(most_common(counts))   # the

# dict.get with default
d = {"a": 1, "b": 2}
print(d.get("a", 0))   # 1
print(d.get("z", 99))  # 99
print(d.get("z"))      # None

# dict from two lists (zip)
keys = ["name", "age", "city"]
vals = ["Alice", "30", "NYC"]
mapping = dict(zip(keys, vals))
print(mapping["name"])   # Alice
print(mapping["age"])    # 30
print(mapping["city"])   # NYC

# Invert a dict
inv = {v: k for k, v in mapping.items()}
print(inv["Alice"])   # name
print(inv["NYC"])     # city
