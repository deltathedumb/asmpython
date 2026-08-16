"""Reading annotations in a chosen FORMAT.

PEP 649 made annotations lazy: a function carries `__annotate__`, a callable
that builds them, and `__annotations__` is what calling it gives. PEP 749
added the question of what form to build them in -- objects, or the text they
were written as -- and this module is that question.

WHAT `STRING` MEANS HERE: the name of whatever the annotation evaluated to.
CPython can answer without evaluating at all, which is how it reports a
forward reference that names nothing; here the value already exists, so the
text is recovered from it. The two agree for every annotation that resolves,
and for one that does not this reports what the frontend left in its place --
a string -- which is the same text.
"""


class Format:
    """The three ways an annotation can be asked for.

    Ordered VALUE < VALUE_WITH_FAKE_GLOBALS < FORWARDREF < STRING, which is
    the order of how much evaluation each avoids, and the order a program
    compares them in.
    """

    VALUE = None
    VALUE_WITH_FAKE_GLOBALS = None
    FORWARDREF = None
    STRING = None


class _FormatValue:
    def __init__(self, name, value):
        self._name_ = name
        self.value = value

    @property
    def name(self):
        return self._name_

    def __repr__(self):
        return "Format." + self._name_

    def __eq__(self, other):
        if isinstance(other, _FormatValue):
            return self.value == other.value
        return self.value == other

    def __ne__(self, other):
        got = self.__eq__(other)
        return got if got is NotImplemented else not got

    def __hash__(self):
        return hash(self.value)


Format.VALUE = _FormatValue("VALUE", 1)
Format.VALUE_WITH_FAKE_GLOBALS = _FormatValue("VALUE_WITH_FAKE_GLOBALS", 2)
Format.FORWARDREF = _FormatValue("FORWARDREF", 3)
Format.STRING = _FormatValue("STRING", 4)


class ForwardRef:
    """A name an annotation used that nothing has defined yet."""

    def __init__(self, arg, owner=None):
        self.__forward_arg__ = arg
        self.__owner__ = owner

    def __repr__(self):
        return "ForwardRef(" + repr(self.__forward_arg__) + ")"

    def __str__(self):
        return self.__forward_arg__


def get_annotations(obj, globals=None, locals=None, eval_str=False,
                    format=None):
    """The annotations of a function or class, in the format asked for."""
    held = dict(getattr(obj, "__annotations__", {}))
    if format is None or format == Format.VALUE:
        return held
    if format == Format.STRING:
        out = {}
        for key in held:
            out[key] = _as_text(held[key])
        return out
    if format == Format.FORWARDREF:
        out = {}
        for key in held:
            value = held[key]
            out[key] = ForwardRef(value) if isinstance(value, str) else value
        return out
    return held


def _as_text(value):
    """An annotation as the text it was written as.

    A STRING ANNOTATION IS ALREADY ITS OWN TEXT -- that is what a forward
    reference is -- so it is returned unchanged rather than quoted again.
    """
    if isinstance(value, str):
        return value
    name = getattr(value, "__name__", None)
    if name is not None:
        return name
    return repr(value)


def call_annotate_function(annotate, format, owner=None):
    """Run a `__annotate__` in the format asked for."""
    made = annotate()
    if format == Format.STRING:
        out = {}
        for key in made:
            out[key] = _as_text(made[key])
        return out
    return made


def annotations_to_string(annotations):
    out = {}
    for key in annotations:
        out[key] = _as_text(annotations[key])
    return out
