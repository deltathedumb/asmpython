# expect:
# a:1
# b:2
# c:3
# a
# b
# c
# 1
# 2
# 3

d = {"a": 1, "b": 2, "c": 3}
for k, v in d.items():
    print(k + ":" + str(v))
for k in d.keys():
    print(k)
for v in d.values():
    print(v)
