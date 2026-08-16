# probes: two instances may reference each other
# expect:
# b
# True
# b
class Node:
    def __init__(self, tag):
        self.tag = tag
        self.other = None


a = Node("a")
b = Node("b")
a.other = b
b.other = a
print(a.other.tag)
print(a.other.other is a)
print(b.other.other.tag)
