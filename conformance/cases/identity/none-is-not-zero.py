# tier: spec
# ref: library/constants.html#None
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
