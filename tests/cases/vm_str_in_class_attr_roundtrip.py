# probes: str stored and re-read from a field
# expect:
# stored
# stored!
# 6
class Box:
    def __init__(self):
        self.slot = ""

    def put(self, v):
        self.slot = v
        return self

    def get(self):
        return self.slot


b = Box().put("stored")
print(b.get())
print(b.get() + "!")
print(len(b.get()))
