# probes: *args/**kwargs forward unchanged
# expect:
# 3
# 6
def target(a, b, c=0):
    return a + b + c


def forward(*args, **kwargs):
    return target(*args, **kwargs)


print(forward(1, 2))
print(forward(1, 2, c=3))
