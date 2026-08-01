# probes: in falls back to iteration when __contains__ is absent
# expect:
# True
# False
class Bag:
    def __iter__(self):
        return iter(["a", "b"])


bag = Bag()
print("a" in bag)
print("z" in bag)
