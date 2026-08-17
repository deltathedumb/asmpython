"""The numeric tower, as classes that answer `isinstance` and nothing else.

`numbers.Integral` has no instances of its own: `1` is one because `int` is
registered under it, not because anything inherits from it. So each class here
is a NAME with a membership test attached, and the test is written out against
the builtin types rather than kept in a registry -- there is one hierarchy, it
is fixed, and writing it down is shorter than the machinery to look it up.

WHAT IS NOT HERE: `register`, and the arithmetic each abstract class declares.
Both matter to a program defining its OWN numeric type, which is a use this
compiler cannot serve yet -- it cannot subclass a builtin number.
"""


class _NumberMeta(type):
    def __instancecheck__(cls, instance):
        return cls._accepts_(instance)

    def __subclasscheck__(cls, subclass):
        # A CLASS, not an instance. Only the builtin numeric types answer
        # True, which is the whole of the registry this stands in for.
        for kind in cls._kinds_:
            if subclass is kind:
                return True
        return False


class Number(metaclass=_NumberMeta):
    _kinds_ = (int, float, complex, bool)

    @staticmethod
    def _accepts_(v):
        # BOOL FIRST, because it is an int and the test below would accept it
        # anyway -- saying so here is what makes the intent readable.
        return isinstance(v, (int, float, complex, bool))


class Complex(Number):
    _kinds_ = (int, float, complex, bool)

    @staticmethod
    def _accepts_(v):
        return isinstance(v, (int, float, complex, bool))


class Real(Complex):
    _kinds_ = (int, float, bool)

    @staticmethod
    def _accepts_(v):
        # A COMPLEX IS NOT REAL even when its imaginary part is zero: the type
        # is what the tower asks about, not the value.
        return isinstance(v, (int, float, bool))


class Rational(Real):
    _kinds_ = (int, bool)

    @staticmethod
    def _accepts_(v):
        return isinstance(v, (int, bool))


class Integral(Rational):
    _kinds_ = (int, bool)

    @staticmethod
    def _accepts_(v):
        return isinstance(v, (int, bool))
