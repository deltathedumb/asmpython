# probes: None is distinguishable from 0
# expect:
# 0
# None
# False
# True
def maybe(flag):
    if flag:
        return 0
    return None


print(maybe(True))
print(maybe(False))
print(maybe(True) is None)
print(maybe(False) is None)
