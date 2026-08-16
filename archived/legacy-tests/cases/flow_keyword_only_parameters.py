# probes: parameters after * are keyword-only
# expect:
# 1-fast
# 1-slow
# positional refused
def build(a, *, mode="fast"):
    return str(a) + "-" + mode


print(build(1))
print(build(1, mode="slow"))
try:
    build(1, "slow")
    print("positional accepted")
except TypeError:
    print("positional refused")
