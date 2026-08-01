# probes: membership compares with __eq__
# expect:
# True
# False
class Tag:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return isinstance(other, Tag) and self.name == other.name

    def __hash__(self):
        return hash(self.name)


print(Tag("a") in [Tag("a"), Tag("b")])
print(Tag("z") in [Tag("a"), Tag("b")])
