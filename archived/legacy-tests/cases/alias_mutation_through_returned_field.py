# probes: mutating a returned field reaches the owner
# expect:
# [1, 2]
# 2
class Box:
    def __init__(self):
        self.items = []

    def get(self):
        return self.items


box = Box()
box.get().append(1)
box.get().append(2)
print(box.items)
print(len(box.items))
