# probes: dict values keep their own kinds
# expect:
# 1
# text
# 2.5
# False
d = {"i": 1, "s": "text", "f": 2.5, "b": False}
print(d["i"])
print(d["s"])
print(d["f"])
print(d["b"])
