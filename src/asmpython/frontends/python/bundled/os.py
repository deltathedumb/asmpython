"""The part of `os` that is about PATHS AS OBJECTS rather than about the OS.

`fspath` and `PathLike` are pure protocol: they ask an object for its
filesystem representation and never touch a filesystem. That is why they can
be here while the rest of `os` cannot -- `getcwd`, `listdir` and `environ`
need the process, and a stub of one would be a wrong answer.

A NAME THIS DOES NOT DEFINE STILL FAILS, and it should: a program reaching for
`os.listdir` is asking for something that genuinely is not here.
"""


class _PathLikeMeta(type):
    def __instancecheck__(cls, instance):
        # STRUCTURAL: an object is path-like because it has `__fspath__`, not
        # because it inherits from anything. That is what the protocol says
        # and what makes a class written before `PathLike` existed satisfy it.
        return hasattr(instance, "__fspath__")

    def __subclasscheck__(cls, subclass):
        return hasattr(subclass, "__fspath__")


class PathLike(metaclass=_PathLikeMeta):
    """An object that can be turned into a path."""

    def __fspath__(self):
        raise NotImplementedError


def fspath(path):
    """The path as `str` or `bytes`, asking the object if it is not one."""
    if isinstance(path, (str, bytes)):
        return path
    hook = getattr(path, "__fspath__", None)
    if hook is None:
        raise TypeError("expected str, bytes or os.PathLike object, not "
                        + type(path).__name__)
    got = hook()
    if not isinstance(got, (str, bytes)):
        raise TypeError("expected " + type(path).__name__
                        + ".__fspath__() to return str or bytes, not "
                        + type(got).__name__)
    return got


#: The separator this build assumes. POSIX, matching `pathlib`'s pure paths.
sep = "/"
altsep = None
extsep = "."
pathsep = ":"
linesep = "\n"
curdir = "."
pardir = ".."
