# expect:
# a
# aa
# aaa
def words(n: int) -> list[str]:
    s = ""
    i = 0
    while i < n:
        s = s + "a"
        yield s
        i = i + 1

for v in words(3):
    print(v)
