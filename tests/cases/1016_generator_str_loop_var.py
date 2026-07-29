# expect:
# a!
# b!
def shout(xs: list[str]) -> list[str]:
    for w in xs:
        yield w + "!"

for v in shout(["a", "b"]):
    print(v)
