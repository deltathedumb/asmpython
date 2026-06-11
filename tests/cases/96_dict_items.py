# expect:
# a 1
# b 2
# 2

# dict.items(): a list of (key, value) pair tuples; `for k, v in d.items()`
# types k as str and v as the dict's value kind.

d = {"a": 1, "b": 2}
for k, v in d.items():
    print(k, v)
print(len(d.items()))
