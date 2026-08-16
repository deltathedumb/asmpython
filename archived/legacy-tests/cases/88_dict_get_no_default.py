# expect:
# 10
# None
# 10
# 99

# dict.get(key) with no default returns None when the key is absent (Python semantics).
# The 2-arg form supplies an explicit default.

d = {"a": 10, "b": 20}
print(d.get("a"))         # 10
print(d.get("missing"))   # None  (no default -> None)
print(d.get("a", 99))     # 10 (present -> value, default ignored)
print(d.get("missing", 99))  # 99
