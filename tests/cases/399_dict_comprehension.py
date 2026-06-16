# expect:
# 1
# 4
# 9
# 3

xs = [1, 2, 3]
d = {str(x): x * x for x in xs}
print(d["1"])
print(d["2"])
print(d["3"])
print(len(d))
