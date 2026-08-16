# probes: hasattr distinguishes present from absent
# expect:
# True
# False
class Thing:
    def __init__(self):
        self.here = 1


t = Thing()
print(hasattr(t, "here"))
print(hasattr(t, "missing"))
