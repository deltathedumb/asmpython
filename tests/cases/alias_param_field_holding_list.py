# probes: mutating a list held by a parameter's field
# expect:
# [1, 2]
class Box:
    def __init__(self):
        self.items = [1]


def mutate(box):
    box.items.append(2)


b = Box()
mutate(b)
print(b.items)
