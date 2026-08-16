# expect:
# axy
# bxy
def joined(xs: list[str]) -> list[str]:
    for w in xs:
        acc = ""
        for c in ["x", "y"]:
            acc = acc + c
        yield w + acc

for v in joined(["a", "b"]):
    print(v)
