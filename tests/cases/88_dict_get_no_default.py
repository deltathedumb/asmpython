# expect:
# 10
# 0
# 10
# 99

# dict.get(key) with no default returns the value, or 0 when the key is absent
# (asmpython's None/unset sentinel). The 2-arg form supplies an explicit default.

d = {"a": 10, "b": 20}
print(d.get("a"))         # 10
print(d.get("missing"))   # 0  (no default -> 0)
print(d.get("a", 99))     # 10 (present -> value, default ignored)
print(d.get("missing", 99))  # 99
