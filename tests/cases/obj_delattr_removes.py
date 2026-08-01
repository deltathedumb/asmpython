# probes: delattr removes an instance attribute
# expect:
# True
# False
class Holder:
    def __init__(self):
        self.field = 1


h = Holder()
print(hasattr(h, "field"))
delattr(h, "field")
print(hasattr(h, "field"))
