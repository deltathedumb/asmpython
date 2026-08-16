# expect:
# 3
# 1
# hi
# fallback


def word_count(words: list) -> dict:
    counts: dict = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    return counts


c: dict = word_count(["a", "b", "a", "a"])
print(c["a"])
print(c["b"])

d: dict = {}
d["x"] = "hi"
print(d.get("x", "fallback"))
print(d.get("y", "fallback"))
