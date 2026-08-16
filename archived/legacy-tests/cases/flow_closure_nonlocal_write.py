# probes: nonlocal rebinds the enclosing local
# expect:
# 2
def counter():
    count = 0

    def bump():
        nonlocal count
        count = count + 1
        return count

    bump()
    bump()
    return count


print(counter())
