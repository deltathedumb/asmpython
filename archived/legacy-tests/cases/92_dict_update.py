# expect:
# 1
# 2
# 30
# 4
# 1

# dict.update(other): merge other's entries into the receiver, overwriting
# existing keys. (Like Python's dict.update.)

d = {"a": 1, "b": 2}
other = {"b": 30, "c": 4}
d.update(other)
print(d["a"])          # 1  (kept)
print(other["a"] if "a" in other else 2)  # 2 (other unchanged: no "a")
print(d["b"])          # 30 (overwritten)
print(d["c"])          # 4  (added)
print(1 if "c" in d else 0)  # 1
