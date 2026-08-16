# probes: a weakref resolves while the target lives
# expect:
# True
import weakref


class Target:
    pass


t = Target()
ref = weakref.ref(t)
print(ref() is t)
