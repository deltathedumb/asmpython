# expect:
# 10
# 20
# 30
# sum: 60
# ---
# first: 10
# second: 20
# ---
# n=3


class Node:
    def __init__(self, v):
        self.v = v

    def value(self):
        return self.v


# Build a list of instances (like AST nodes).
nodes = [Node(10), Node(20), Node(30)]

# Iterate and call methods.
total = 0
for n in nodes:
    print(n.value())
    total = total + n.value()
print("sum:", total)

print("---")

# Index into the list.
print("first:", nodes[0].value())
print("second:", nodes[1].value())

print("---")

# Length of the list.
print("n=" + str(len(nodes)))
