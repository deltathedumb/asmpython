# probes: rebinding a parameter's field reaches the caller
# expect:
# [7, 8]
class Box:
    def __init__(self):
        self.items = [1]


def replace(box):
    box.items = [7, 8]


b = Box()
replace(b)
print(b.items)
