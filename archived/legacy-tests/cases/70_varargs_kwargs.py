# expect:
# 6
# 0
# hi-there
# w=10 label=box
def total(*nums: int) -> int:
    acc = 0
    for n in nums:
        acc = acc + n
    return acc


def joiner(sep: str, *parts: str) -> str:
    out = ""
    for p in parts:
        if out == "":
            out = p
        else:
            out = out + sep + p
    return out


def describe(width: int, *, label: str) -> str:
    return "w=" + str(width) + " label=" + label


print(total(1, 2, 3))
print(total())
print(joiner("-", "hi", "there"))
print(describe(10, label="box"))
