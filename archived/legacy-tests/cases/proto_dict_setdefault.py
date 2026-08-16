# probes: setdefault inserts only when absent
# expect:
# 1
# 2
# {'a': 1, 'b': 2}
d = {"a": 1}
print(d.setdefault("a", 99))
print(d.setdefault("b", 2))
print(d)
