# probes: __getattr__ handles a missing attribute
# expect:
# dyn_foo
# dyn_bar
class Dynamic:
    def __getattr__(self, name):
        return "dyn_" + name


d = Dynamic()
print(d.foo)
print(d.bar)
