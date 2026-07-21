# expect:
# 2


class Node:
    def __init__(self):
        self.children = []

    def walk(self):
        yield self
        for child in self.children:
            yield child


root = Node()
root.children.append(Node())
print(len(root.walk()))
