# probes: a closure mutates a captured container
# expect:
# ['a', 'b']
def collector():
    seen = []

    def add(v):
        seen.append(v)
        return len(seen)

    add("a")
    add("b")
    return seen


print(collector())
