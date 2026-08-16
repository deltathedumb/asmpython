# probes: an instance read from a list is the same object
# expect:
# True
# changed
class Node:
    def __init__(self, tag):
        self.tag = tag


first = Node("a")
nodes = [first, Node("b")]
print(nodes[0] is first)
nodes[0].tag = "changed"
print(first.tag)
