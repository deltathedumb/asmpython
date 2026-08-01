# probes: __getattr__ is skipped for real attributes
# expect:
# real-value
# dyn_other
class Dynamic:
    def __init__(self):
        self.real = "real-value"

    def __getattr__(self, name):
        return "dyn_" + name


d = Dynamic()
print(d.real)
print(d.other)
