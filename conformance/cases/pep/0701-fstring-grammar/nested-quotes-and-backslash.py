# tier: spec
# ref: reference/lexical_analysis.html#f-strings
# min-python: 3.12
# expect:
# v
# nested
# 'a\nb'
d = {"k": "v"}
print(f"{d["k"]}")
print(f"{"nested"}")
items = ["a", "b"]
print(f"{"\n".join(items)!r}")
