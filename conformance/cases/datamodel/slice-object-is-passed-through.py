# tier: spec
# ref: reference/datamodel.html#object.__getitem__
# expect:
# ('index', 1)
# ('slice', 1, 2, None)
# ('slice', None, None, 2)
# ('index', (slice(1, 2, None), 3))
class C:
    def __getitem__(self, key):
        if isinstance(key, slice):
            return ("slice", key.start, key.stop, key.step)
        return ("index", key)

c = C()
print(c[1])
print(c[1:2])
print(c[::2])
print(c[1:2, 3])
