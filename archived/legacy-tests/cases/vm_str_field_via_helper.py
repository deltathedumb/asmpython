# probes: a str field read through a helper stays a str
# expect:
# b
# leaf:b
class Leaf:
    def __init__(self, tag):
        self.tag = tag


def newest(seq):
    return seq[len(seq) - 1]


leaves = [Leaf("a"), Leaf("b")]
found = newest(leaves)
print(found.tag)
print("leaf:" + found.tag)
