# expect:
# [1] [2]
def f(x, acc=None):
    acc = acc or []
    acc.append(x)
    return acc


print(f(1), f(2))
# the `acc = acc or []` idiom yields empty lists ([] []) under asmpython, not [1] [2].
