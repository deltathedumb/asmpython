# probes: parameters before / are positional-only
# expect:
# 1-2
# 1-3
# keyword refused
def build(a, /, b):
    return str(a) + "-" + str(b)


print(build(1, 2))
print(build(1, b=3))
try:
    build(a=1, b=2)
    print("keyword accepted")
except TypeError:
    print("keyword refused")
