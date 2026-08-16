"""Abstract base classes.

`ABCMeta` is a metaclass and nothing more: it collects the names the body
marked abstract and refuses to instantiate a class that still has any. Both
halves are ordinary Python -- `type.__new__` through `super()`, and a
`__call__` that decides what calling the class does -- which is the whole
reason this is written here rather than in the runtime.

WHAT IS NOT HERE: the `__subclasshook__` protocol, which lets a class decide
structurally whether something counts as a subclass. `register` is, and it is
the half programs actually call.
"""


def abstractmethod(funcobj):
    """Mark a method as one a concrete subclass must define.

    The mark is an attribute on the function, which is where `ABCMeta` looks
    for it -- exactly as in CPython, so a decorator stack that puts
    `@classmethod` outside it still works if the wrapper forwards the flag.
    """
    funcobj.__isabstractmethod__ = True
    return funcobj


class ABCMeta(type):
    def __new__(mcls, name, bases, namespace):
        cls = super().__new__(mcls, name, bases, namespace)
        abstracts = []
        for key in namespace:
            if getattr(namespace[key], "__isabstractmethod__", False):
                abstracts.append(key)
        # A NAME THE BASE LEFT ABSTRACT IS STILL ABSTRACT unless this body
        # bound something concrete over it. Looked up on the CLASS, not in
        # this namespace, so an override two levels up counts.
        for base in bases:
            for key in getattr(base, "__abstractmethods__", ()):
                if key in abstracts:
                    continue
                if getattr(getattr(cls, key, None), "__isabstractmethod__",
                           False):
                    abstracts.append(key)
        cls.__abstractmethods__ = frozenset(abstracts)
        # ONE LIST PER CLASS, made here rather than as a class attribute of
        # `ABCMeta`: a single shared list would make every ABC in the program
        # answer for every registration any of them accepted.
        cls._abc_registry = []
        return cls

    def register(cls, subclass):
        """Declare `subclass` a subclass of `cls` WITHOUT INHERITANCE.

        Nothing about `subclass` changes -- its `__mro__` does not mention
        `cls` -- so the claim lives here and only `isinstance` and
        `issubclass` are taught to consult it.
        """
        cls._abc_registry.append(subclass)
        return subclass

    def __subclasscheck__(cls, subclass):
        # THE REAL HIERARCHY FIRST, walked by hand: asking `issubclass` here
        # would come straight back to this hook and never stop.
        for entry in getattr(subclass, "__mro__", ()):
            if entry is cls:
                return True
        for entry in cls._abc_registry:
            if subclass is entry:
                return True
            # A SUBCLASS OF A REGISTERED CLASS COUNTS TOO -- registration is
            # about the whole subtree, not the one class named.
            for walk in getattr(subclass, "__mro__", ()):
                if walk is entry:
                    return True
        return False

    def __instancecheck__(cls, instance):
        return cls.__subclasscheck__(type(instance))

    def __call__(cls, *args, **kwargs):
        missing = cls.__abstractmethods__
        if missing:
            names = sorted(missing)
            listed = ""
            for name in names:
                if listed:
                    listed = listed + ", "
                listed = listed + "'" + name + "'"
            plural = "s" if len(names) != 1 else ""
            raise TypeError("Can't instantiate abstract class " + cls.__name__
                            + " without an implementation for abstract method"
                            + plural + " " + listed)
        return super().__call__(*args, **kwargs)


class ABC(metaclass=ABCMeta):
    """A base whose subclasses get `ABCMeta` without repeating `metaclass=`."""
