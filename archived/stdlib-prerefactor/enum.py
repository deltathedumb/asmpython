"""Enumerations.

An `Enum` is a class whose body names constants and whose metaclass turns each
of them into an INSTANCE of that class. That is the whole of it: `Color.RED`
is not the integer 1, it is an object that knows it is called RED and that its
value is 1, and `Color(1)` finds it by that value.

The metaclass is what does the rewriting, which is why this could not be
written before `ABCMeta` could -- both need a `__new__` that reshapes the
namespace it is handed and a class that inherits its metaclass from a base.

WHAT IS NOT HERE: `Flag`, `StrEnum`, aliases resolving to the first member of
equal value, and `@unique`. `IntEnum` IS, because the comparison it promises
is the one programs actually depend on.
"""


class auto:
    """A value the metaclass fills in: one past the highest so far."""


class _EnumMember:
    """What a name in an enum body becomes.

    NOT a class of its own -- every member is an instance of the enum class,
    so `isinstance(Color.RED, Color)` holds. This is only the marker the
    metaclass leaves while it is deciding.
    """

    def __init__(self, value):
        self.value = value


class EnumMeta(type):
    def __new__(mcls, name, bases, namespace):
        # WHICH NAMES ARE MEMBERS: the ones that are neither dunders nor
        # callables. A method in an enum body stays a method -- that is how
        # `Color.describe()` works -- and only the plain constants become
        # members.
        chosen = []
        rest = {}
        highest = 0
        for key in namespace:
            value = namespace[key]
            if key.startswith("_") or callable(value) or _is_descriptor(value):
                rest[key] = value
                continue
            if isinstance(value, auto):
                highest = highest + 1
                value = highest
            elif isinstance(value, int) and not isinstance(value, bool):
                # `auto()` COUNTS FROM THE HIGHEST WRITTEN VALUE, not from the
                # number of members: `RED = 5` then `GREEN = auto()` is 6.
                if value > highest:
                    highest = value
            chosen.append((key, value))
            rest[key] = None
        cls = super().__new__(mcls, name, bases, rest)
        members = []
        by_value = {}
        by_name = {}
        for pair in chosen:
            member = object.__new__(cls)
            member._name_ = pair[0]
            member._value_ = pair[1]
            setattr(cls, pair[0], member)
            members.append(member)
            by_value[pair[1]] = member
            by_name[pair[0]] = member
        cls._member_list_ = members
        cls._value2member_ = by_value
        cls._member_map_ = by_name
        return cls

    def __call__(cls, *args, **kwargs):
        """`Color(1)` LOOKS A MEMBER UP; it does not build one.

        An enum class is not instantiable by a program -- the members were all
        made when the class was, so calling it is a query.
        """
        if len(args) == 1 and not kwargs:
            found = cls._value2member_
            if args[0] in found:
                return found[args[0]]
            raise ValueError(str(args[0]) + " is not a valid " + cls.__name__)
        return super().__call__(*args, **kwargs)

    def __iter__(cls):
        return iter(cls._member_list_)

    def __len__(cls):
        return len(cls._member_list_)

    def __getitem__(cls, key):
        return cls._member_map_[key]

    def __contains__(cls, item):
        return item in cls._member_list_


def _is_descriptor(value):
    return (hasattr(value, "__get__") or hasattr(value, "__set__")
            or hasattr(value, "__delete__"))


class Enum(metaclass=EnumMeta):
    @property
    def name(self):
        return self._name_

    @property
    def value(self):
        return self._value_

    def __repr__(self):
        return "<" + type(self).__name__ + "." + self._name_ + ": " \
               + repr(self._value_) + ">"

    def __str__(self):
        return type(self).__name__ + "." + self._name_

    def __hash__(self):
        return hash(self._name_)


class IntEnum(Enum):
    """An enum whose members ARE comparable with, and arithmetic on, ints.

    CPython gets this by inheriting `int`; there is no subclassing of a
    builtin here, so the operators are written out. What a program depends on
    -- `Num.ONE == 1` and `Num.ONE + 1` -- is exactly what is defined.
    """

    def __eq__(self, other):
        if isinstance(other, IntEnum):
            return self is other
        return self._value_ == other

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self._value_)

    def __int__(self):
        return self._value_

    def __index__(self):
        return self._value_

    def __lt__(self, other):
        return self._value_ < _as_int(other)

    def __le__(self, other):
        return self._value_ <= _as_int(other)

    def __gt__(self, other):
        return self._value_ > _as_int(other)

    def __ge__(self, other):
        return self._value_ >= _as_int(other)

    def __add__(self, other):
        return self._value_ + _as_int(other)

    def __radd__(self, other):
        return _as_int(other) + self._value_

    def __sub__(self, other):
        return self._value_ - _as_int(other)

    def __rsub__(self, other):
        return _as_int(other) - self._value_

    def __mul__(self, other):
        return self._value_ * _as_int(other)

    def __rmul__(self, other):
        return _as_int(other) * self._value_


def _as_int(value):
    return value._value_ if isinstance(value, IntEnum) else value
