# expect:
# 2
# somnia.Root


class Node:
    def __init__(self, type_name):
        self.type_name = type_name


nodes = [Node("somnia.Root"), Node("somnia.Child")]
values = [
    node.type_name
    for node in nodes
    if node.type_name.startswith("somnia.")
]
print(len(values))
print(values[0])
