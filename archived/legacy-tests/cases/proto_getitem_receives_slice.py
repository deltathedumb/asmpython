# probes: __getitem__ receives a slice object
# expect:
# (1, 5, 2)
class Probe:
    def __getitem__(self, key):
        return (key.start, key.stop, key.step)


print(Probe()[1:5:2])
