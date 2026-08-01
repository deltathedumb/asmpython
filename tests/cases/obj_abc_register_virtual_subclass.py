# probes: ABCMeta.register creates a virtual subclass
# expect:
# True
# True
import abc


class Drawable(abc.ABC):
    pass


class Circle:
    pass


Drawable.register(Circle)
print(issubclass(Circle, Drawable))
print(isinstance(Circle(), Drawable))
