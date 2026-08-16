# probes: a dict key keeps its identity through an opaque param
# expect:
# 7
# 7
# 9
# 9
# three
# three
def get(d, k):
    return d[k]


def put(d, k, v):
    d[k] = v


d = {}
d["foo"] = 7
print(d["foo"])
print(get(d, "foo"))

e = {}
put(e, "bar", 9)
print(e["bar"])
print(get(e, "bar"))

n = {}
n[3] = "three"
print(n[3])
print(get(n, 3))
