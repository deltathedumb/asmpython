# probes: dict.get returns its default for a missing key
# expect:
# 1
# None
# fallback
d = {"a": 1}
print(d.get("a"))
print(d.get("b"))
print(d.get("b", "fallback"))
