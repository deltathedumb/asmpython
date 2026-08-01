# tier: spec
# ref: reference/executionmodel.html#naming-and-binding
# expect:
# 1
# unbound
def f(flag):
    if flag:
        v = 1
    return locals().get("v", "unbound")

print(f(True))
print(f(False))
