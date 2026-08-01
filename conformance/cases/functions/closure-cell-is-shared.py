# tier: spec
# ref: reference/expressions.html#atom-identifiers
# expect:
# 0
# 2
def make():
    n = 0
    def bump():
        nonlocal n
        n += 1
        return n
    def read():
        return n
    return bump, read

bump, read = make()
print(read())
bump()
bump()
print(read())
