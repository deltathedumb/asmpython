# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [1, 99]
# [0]
# [1, 99]
def mutate(seq):
    seq.append(99)

def rebind(seq):
    seq = [0]
    return seq

xs = [1]
mutate(xs)
print(xs)
print(rebind(xs))
print(xs)
