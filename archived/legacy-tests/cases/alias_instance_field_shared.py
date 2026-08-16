# probes: a list stored in a field stays shared
# expect:
# [1, 2]
# 2
class Box:
    def __init__(self, items):
        self.items = items


items = [1]
box = Box(items)
items.append(2)
print(box.items)
print(len(box.items))
