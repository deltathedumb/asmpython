# probes: a field read back is the same object
# expect:
# True
# True
class Box:
    def __init__(self, payload):
        self.payload = payload

    def get(self):
        return self.payload


items = [1]
box = Box(items)
print(box.get() is items)
print(box.payload is items)
