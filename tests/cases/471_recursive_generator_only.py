# expect:
# 2
# Generator isolation generation 2.


class Node:
    def __init__(self):
        self.children = []

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


root = Node()
root.children.append(Node())
print(len(root.walk()))
