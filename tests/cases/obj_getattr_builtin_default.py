# probes: getattr returns its default when absent
# expect:
# 1
# fallback
class Thing:
    def __init__(self):
        self.here = 1


t = Thing()
print(getattr(t, "here", "fallback"))
print(getattr(t, "missing", "fallback"))
