# probes: an object-annotated parameter still aliases
# expect:
# [1, 2]
# True
def add(xs: object) -> object:
    xs.append(2)
    return xs


a = [1]
returned = add(a)
print(a)
print(returned is a)
