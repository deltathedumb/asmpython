"""Generate object-model conformance probes.

asmpython compiles classes ahead of time. A class in CPython is a runtime
object with a metatype, an MRO, and an attribute lookup that consults data
descriptors, then the instance dict, then non-data descriptors, then the MRO --
a protocol, not a record layout. Everywhere the compiler models a class as a
struct with a vtable, the difference shows up as one of a small set of
divergences: an inherited `classmethod` receiving the wrong `cls`, a `property`
read as a plain attribute, a descriptor bypassed, `__slots__` not enforced,
`__mro__` linearized in declaration order rather than by C3.

The corpus reaches this area only incidentally -- `class_*.py` cases are
programs that use classes, so they conflate "the object model is wrong" with
"this program is wrong". FAILURE_AUDIT.md's largest bucket is 64 cases that
were never root-caused at all, and its rank-21/22 entries (`sort ignores
__lt__`, `__getattr__ unsupported`) are single-case footnotes to what is
plainly a wider gap.

Each probe here isolates one rule of the model. They are ordinary Python -- no
probe depends on CPython internals, only on documented language semantics --
so any of them failing is a real conformance gap and not an implementation
detail.

Usage: python gen_obj_cases.py <tests/cases dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _emit import CaseSet, main  # noqa: E402

CASES = CaseSet("probes")
case = CASES.case


# ---------------------------------------------------------------------------
# inheritance and method resolution
# ---------------------------------------------------------------------------

case("obj_inherited_method", "a subclass inherits an undefined method", r'''
class Base:
    def speak(self):
        return "base"


class Child(Base):
    pass


print(Child().speak())
''')

case("obj_override_method", "a subclass method shadows the base method", r'''
class Base:
    def speak(self):
        return "base"


class Child(Base):
    def speak(self):
        return "child"


print(Child().speak())
print(Base().speak())
''')

case("obj_super_forwards_to_base", "super() reaches the shadowed base method", r'''
class Base:
    def speak(self):
        return "base"


class Child(Base):
    def speak(self):
        return "child+" + super().speak()


print(Child().speak())
''')

case("obj_super_init_chain", "super().__init__ runs the base initializer", r'''
class Base:
    def __init__(self, name):
        self.name = name


class Child(Base):
    def __init__(self, name, extra):
        super().__init__(name)
        self.extra = extra


c = Child("ada", 7)
print(c.name)
print(c.extra)
''')

case("obj_super_three_levels", "super() walks a three-deep chain", r'''
class A:
    def tag(self):
        return "A"


class B(A):
    def tag(self):
        return "B" + super().tag()


class C(B):
    def tag(self):
        return "C" + super().tag()


print(C().tag())
''')

case("obj_mro_diamond_order", "__mro__ is the C3 linearization", r'''
class A:
    pass


class B(A):
    pass


class C(A):
    pass


class D(B, C):
    pass


print([cls.__name__ for cls in D.__mro__])
''')

case("obj_diamond_cooperative_super", "diamond super() visits each class once", r'''
class A:
    def run(self):
        return ["A"]


class B(A):
    def run(self):
        return ["B"] + super().run()


class C(A):
    def run(self):
        return ["C"] + super().run()


class D(B, C):
    def run(self):
        return ["D"] + super().run()


print(D().run())
''')

case("obj_multiple_inheritance_attr", "attribute lookup follows the MRO left to right", r'''
class Left:
    kind = "left"


class Right:
    kind = "right"
    other = "only-right"


class Both(Left, Right):
    pass


print(Both.kind)
print(Both.other)
''')

case("obj_isinstance_subclass", "isinstance accepts an instance of a subclass", r'''
class Base:
    pass


class Child(Base):
    pass


print(isinstance(Child(), Base))
print(isinstance(Base(), Child))
''')

case("obj_issubclass_relation", "issubclass reports the class relation", r'''
class Base:
    pass


class Child(Base):
    pass


print(issubclass(Child, Base))
print(issubclass(Base, Child))
print(issubclass(Child, Child))
''')

case("obj_isinstance_type_tuple", "isinstance accepts a tuple of types", r'''
print(isinstance(1, (str, int)))
print(isinstance("s", (str, int)))
print(isinstance(1.5, (str, int)))
''')


# ---------------------------------------------------------------------------
# properties
# ---------------------------------------------------------------------------

case("obj_property_getter", "a property is read without a call", r'''
class Circle:
    def __init__(self, r):
        self.r = r

    @property
    def diameter(self):
        return self.r * 2


print(Circle(3).diameter)
''')

case("obj_property_setter", "a property setter intercepts assignment", r'''
class Temp:
    def __init__(self):
        self._c = 0

    @property
    def celsius(self):
        return self._c

    @celsius.setter
    def celsius(self, value):
        self._c = value * 1


t = Temp()
t.celsius = 21
print(t.celsius)
print(t._c)
''')

case("obj_property_deleter", "a property deleter runs on del", r'''
class Slot:
    def __init__(self):
        self._v = "set"

    @property
    def v(self):
        return self._v

    @v.deleter
    def v(self):
        self._v = "deleted"


s = Slot()
del s.v
print(s._v)
''')

case("obj_property_inherited", "a subclass inherits a property", r'''
class Base:
    @property
    def label(self):
        return "base-label"


class Child(Base):
    pass


print(Child().label)
''')

case("obj_property_overrides_instance", "a property wins over an instance dict entry", r'''
class Thing:
    @property
    def name(self):
        return "from-property"


t = Thing()
try:
    t.name = "from-instance"
    print("assignment allowed")
except AttributeError:
    print("assignment refused")
print(t.name)
''')


# ---------------------------------------------------------------------------
# descriptors
# ---------------------------------------------------------------------------

case("obj_descriptor_get", "__get__ is invoked on attribute read", r'''
class Const:
    def __get__(self, obj, owner):
        return "descriptor-value"


class Holder:
    field = Const()


print(Holder().field)
''')

case("obj_descriptor_set", "__set__ intercepts attribute assignment", r'''
class Recorder:
    def __init__(self):
        self.stored = None

    def __get__(self, obj, owner):
        return self.stored

    def __set__(self, obj, value):
        self.stored = "wrapped:" + str(value)


class Holder:
    field = Recorder()


h = Holder()
h.field = 5
print(h.field)
''')

case("obj_data_descriptor_precedence", "a data descriptor beats the instance dict", r'''
class Data:
    def __get__(self, obj, owner):
        return "descriptor"

    def __set__(self, obj, value):
        obj.__dict__["field"] = value


class Holder:
    field = Data()


h = Holder()
h.field = "instance"
print(h.__dict__["field"])
print(h.field)
''')

case("obj_non_data_descriptor_yields", "a non-data descriptor loses to the instance dict", r'''
class NonData:
    def __get__(self, obj, owner):
        return "descriptor"


class Holder:
    field = NonData()


h = Holder()
print(h.field)
h.__dict__["field"] = "instance"
print(h.field)
''')

case("obj_set_name_hook", "__set_name__ receives the attribute name", r'''
class Named:
    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, owner):
        return self.name


class Holder:
    first = Named()
    second = Named()


h = Holder()
print(h.first)
print(h.second)
''')


# ---------------------------------------------------------------------------
# __slots__
# ---------------------------------------------------------------------------

case("obj_slots_store_and_read", "a __slots__ attribute stores and reads back", r'''
class Point:
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y


p = Point(1, 2)
print(p.x)
print(p.y)
''')

case("obj_slots_reject_new_attr", "__slots__ refuses an undeclared attribute", r'''
class Point:
    __slots__ = ("x",)

    def __init__(self):
        self.x = 1


p = Point()
try:
    p.z = 3
    print("accepted")
except AttributeError:
    print("refused")
''')

case("obj_slots_no_instance_dict", "a __slots__ instance has no __dict__", r'''
class Point:
    __slots__ = ("x",)


print(hasattr(Point(), "__dict__"))
''')

case("obj_slots_inherited", "a subclass sees the base's slots", r'''
class Base:
    __slots__ = ("a",)


class Child(Base):
    __slots__ = ("b",)


c = Child()
c.a = 1
c.b = 2
print(c.a)
print(c.b)
''')


# ---------------------------------------------------------------------------
# classmethod / staticmethod
# ---------------------------------------------------------------------------

case("obj_classmethod_receives_class", "a classmethod receives the class", r'''
class Widget:
    @classmethod
    def kind(cls):
        return cls.__name__


print(Widget.kind())
print(Widget().kind())
''')

case("obj_classmethod_inherited_cls", "an inherited classmethod receives the SUBclass", r'''
class Base:
    @classmethod
    def kind(cls):
        return cls.__name__


class Child(Base):
    pass


print(Base.kind())
print(Child.kind())
''')

case("obj_classmethod_alternative_constructor", "a classmethod can build an instance", r'''
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    @classmethod
    def origin(cls):
        return cls(0, 0)


p = Point.origin()
print(p.x)
print(p.y)
''')

case("obj_staticmethod_no_self", "a staticmethod takes no implicit argument", r'''
class MathBox:
    @staticmethod
    def add(a, b):
        return a + b


print(MathBox.add(2, 3))
print(MathBox().add(4, 5))
''')

case("obj_classmethod_super", "super() works inside a classmethod", r'''
class Base:
    @classmethod
    def tag(cls):
        return "base"


class Child(Base):
    @classmethod
    def tag(cls):
        return "child+" + super().tag()


print(Child.tag())
''')


# ---------------------------------------------------------------------------
# metaclasses and dynamic class creation
# ---------------------------------------------------------------------------

case("obj_metaclass_new_adds_attr", "a metaclass __new__ can edit the namespace", r'''
class Tagging(type):
    def __new__(mcls, name, bases, namespace):
        namespace["tag"] = "tagged-" + name
        return super().__new__(mcls, name, bases, namespace)


class Widget(metaclass=Tagging):
    pass


print(Widget.tag)
''')

case("obj_metaclass_is_type_of_class", "type(cls) is its metaclass", r'''
class Meta(type):
    pass


class Widget(metaclass=Meta):
    pass


print(type(Widget).__name__)
print(type(Widget()).__name__)
''')

case("obj_metaclass_call_intercepts", "a metaclass __call__ wraps instantiation", r'''
class Counting(type):
    made = 0

    def __call__(cls, *args, **kwargs):
        Counting.made = Counting.made + 1
        return super().__call__(*args, **kwargs)


class Widget(metaclass=Counting):
    pass


Widget()
Widget()
print(Counting.made)
''')

case("obj_type_three_arg", "type(name, bases, dict) builds a class", r'''
Dynamic = type("Dynamic", (), {"greet": lambda self: "hi"})
d = Dynamic()
print(Dynamic.__name__)
print(d.greet())
''')

case("obj_init_subclass_hook", "__init_subclass__ runs for each subclass", r'''
class Registry:
    seen = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        Registry.seen.append(cls.__name__)


class A(Registry):
    pass


class B(Registry):
    pass


print(Registry.seen)
''')


# ---------------------------------------------------------------------------
# dunder protocols that define what an object IS
# ---------------------------------------------------------------------------

case("obj_repr_used_when_no_str", "print falls back to __repr__", r'''
class Tagged:
    def __repr__(self):
        return "<Tagged>"


print(Tagged())
''')

case("obj_str_preferred_over_repr", "print prefers __str__ over __repr__", r'''
class Tagged:
    def __repr__(self):
        return "<repr>"

    def __str__(self):
        return "<str>"


print(Tagged())
print(repr(Tagged()))
''')

case("obj_repr_used_inside_container", "a container renders elements with repr", r'''
class Tagged:
    def __repr__(self):
        return "<repr>"

    def __str__(self):
        return "<str>"


print([Tagged()])
''')

case("obj_default_eq_is_identity", "without __eq__, equality is identity", r'''
class Plain:
    pass


a = Plain()
b = Plain()
print(a == a)
print(a == b)
''')

case("obj_eq_defined_used", "__eq__ decides ==", r'''
class Version:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n


print(Version(1) == Version(1))
print(Version(1) == Version(2))
print(Version(1) != Version(2))
''')

case("obj_eq_without_hash_unhashable", "defining __eq__ alone clears __hash__", r'''
class Version:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n


try:
    hash(Version(1))
    print("hashable")
except TypeError:
    print("unhashable")
''')

case("obj_hash_and_eq_dedupe", "__hash__ plus __eq__ makes instances dict keys", r'''
class Key:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n

    def __hash__(self):
        return hash(self.n)


table = {}
table[Key(1)] = "first"
table[Key(1)] = "second"
print(len(table))
print(table[Key(1)])
''')

case("obj_lt_drives_sorted", "sorted() uses __lt__ on instances", r'''
class Version:
    def __init__(self, n):
        self.n = n

    def __lt__(self, other):
        return self.n < other.n

    def __repr__(self):
        return "v" + str(self.n)


print(sorted([Version(3), Version(1), Version(2)]))
''')

case("obj_lt_drives_min_max", "min/max use __lt__ on instances", r'''
class Version:
    def __init__(self, n):
        self.n = n

    def __lt__(self, other):
        return self.n < other.n


print(min([Version(3), Version(1)]).n)
print(max([Version(3), Version(1)]).n)
''')

case("obj_call_dunder", "__call__ makes an instance callable", r'''
class Adder:
    def __init__(self, base):
        self.base = base

    def __call__(self, n):
        return self.base + n


add5 = Adder(5)
print(add5(3))
print(callable(add5))
''')

case("obj_len_dunder", "len() dispatches to __len__", r'''
class Bag:
    def __init__(self, n):
        self.n = n

    def __len__(self):
        return self.n


print(len(Bag(4)))
''')

case("obj_bool_via_len", "truthiness falls back to __len__", r'''
class Bag:
    def __init__(self, n):
        self.n = n

    def __len__(self):
        return self.n


print(bool(Bag(0)))
print(bool(Bag(2)))
''')

case("obj_bool_dunder_wins", "__bool__ overrides __len__ for truthiness", r'''
class Odd:
    def __len__(self):
        return 0

    def __bool__(self):
        return True


print(bool(Odd()))
''')

case("obj_getattr_fallback", "__getattr__ handles a missing attribute", r'''
class Dynamic:
    def __getattr__(self, name):
        return "dyn_" + name


d = Dynamic()
print(d.foo)
print(d.bar)
''')

case("obj_getattr_not_called_when_present", "__getattr__ is skipped for real attributes", r'''
class Dynamic:
    def __init__(self):
        self.real = "real-value"

    def __getattr__(self, name):
        return "dyn_" + name


d = Dynamic()
print(d.real)
print(d.other)
''')

case("obj_setattr_intercepts", "__setattr__ sees every assignment", r'''
class Logged:
    def __init__(self):
        object.__setattr__(self, "log", [])

    def __setattr__(self, name, value):
        self.log.append(name)
        object.__setattr__(self, name, value)


o = Logged()
o.a = 1
o.b = 2
print(o.log)
print(o.a)
''')

case("obj_getattribute_intercepts_all", "__getattribute__ sees every read", r'''
class Watched:
    def __init__(self):
        object.__setattr__(self, "value", 7)

    def __getattribute__(self, name):
        if name == "value":
            return 99
        return object.__getattribute__(self, name)


print(Watched().value)
''')

case("obj_hasattr_reports_presence", "hasattr distinguishes present from absent", r'''
class Thing:
    def __init__(self):
        self.here = 1


t = Thing()
print(hasattr(t, "here"))
print(hasattr(t, "missing"))
''')

case("obj_getattr_builtin_default", "getattr returns its default when absent", r'''
class Thing:
    def __init__(self):
        self.here = 1


t = Thing()
print(getattr(t, "here", "fallback"))
print(getattr(t, "missing", "fallback"))
''')

case("obj_format_dunder", "format() dispatches to __format__", r'''
class Money:
    def __init__(self, amount):
        self.amount = amount

    def __format__(self, spec):
        return "$" + format(self.amount, spec)


print(format(Money(3), ".2f"))
print(format(Money(5), "d"))
''')

case("obj_index_dunder", "__index__ lets an object index a sequence", r'''
class Two:
    def __index__(self):
        return 2


print([10, 20, 30][Two()])
''')

case("obj_new_override", "__new__ can return a prepared instance", r'''
class Tagged:
    def __new__(cls, *args, **kwargs):
        obj = super().__new__(cls)
        obj.created = True
        return obj

    def __init__(self, name):
        self.name = name


t = Tagged("x")
print(t.created)
print(t.name)
''')

case("obj_class_attr_shared", "a class attribute is shared until shadowed", r'''
class Counter:
    total = 0


a = Counter()
b = Counter()
Counter.total = 5
print(a.total)
print(b.total)
a.total = 9
print(a.total)
print(b.total)
print(Counter.total)
''')

case("obj_instance_dict_contents", "vars() exposes the instance dict", r'''
class Point:
    def __init__(self):
        self.x = 1
        self.y = 2


print(vars(Point()))
''')

case("obj_bound_method_self", "a bound method remembers its receiver", r'''
class Greeter:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return "hi " + self.name


bound = Greeter("ada").greet
print(bound())
print(bound.__self__.name)
''')

case("obj_class_of_instance", "__class__ and type() agree", r'''
class Widget:
    pass


w = Widget()
print(type(w).__name__)
print(w.__class__.__name__)
print(type(w) is Widget)
''')

case("obj_abstract_method_blocks_instantiation", "an abstractmethod blocks instantiation", r'''
import abc


class Shape(abc.ABC):
    @abc.abstractmethod
    def area(self):
        ...


class Square(Shape):
    def __init__(self, side):
        self.side = side

    def area(self):
        return self.side * self.side


try:
    Shape()
    print("abstract instantiated")
except TypeError:
    print("abstract refused")
print(Square(3).area())
''')


# ===========================================================================
# Wave 2. Wave 1 came back 53% failing with 14 of its 62 probes crashing the
# compiled binary outright, so this area is the opposite of sampled out. This
# wave covers what wave 1 skipped: subclassing the BUILTIN types (where the
# object model meets the value model), the parts of class creation that are
# ordinary code running at definition time, and the dunders that make an
# instance convertible rather than callable.
# ===========================================================================

case("obj_subclass_of_list", "a list subclass inherits list behaviour", r'''
class Stack(list):
    def push(self, value):
        self.append(value)
        return self


s = Stack()
s.push(1).push(2)
print(len(s))
print(s[0])
print(list(s))
''')

case("obj_subclass_of_dict", "a dict subclass inherits dict behaviour", r'''
class Config(dict):
    def get_or(self, key, fallback):
        return self[key] if key in self else fallback


c = Config()
c["a"] = 1
print(c["a"])
print(len(c))
print(c.get_or("b", "default"))
''')

case("obj_dict_missing_hook", "__missing__ handles an absent dict key", r'''
class Defaulting(dict):
    def __missing__(self, key):
        return "default-" + key


d = Defaulting()
d["present"] = "real"
print(d["present"])
print(d["absent"])
''')

case("obj_subclass_of_str", "a str subclass keeps str behaviour", r'''
class Name(str):
    def shout(self):
        return self.upper()


n = Name("ada")
print(len(n))
print(n.shout())
print(n + "!")
''')

case("obj_subclass_of_exception", "an Exception subclass is catchable as Exception", r'''
class AppError(Exception):
    pass


try:
    raise AppError("failed")
except Exception as err:
    print(type(err).__name__)
    print(str(err))
''')

case("obj_name_mangling", "a __private name is mangled per class", r'''
class Holder:
    def __init__(self):
        self.__secret = "hidden"

    def reveal(self):
        return self.__secret


h = Holder()
print(h.reveal())
print(hasattr(h, "_Holder__secret"))
print(hasattr(h, "__secret"))
''')

case("obj_class_body_runs_at_definition", "a class body executes when defined", r'''
class Built:
    print("body ran")
    computed = 2 * 3


print(Built.computed)
''')

case("obj_annotations_recorded", "__annotations__ records declared types", r'''
class Record:
    name: str
    count: int


print(sorted(Record.__annotations__.keys()))
''')

case("obj_docstring_recorded", "__doc__ holds the class docstring", r'''
class Documented:
    """The documentation."""


print(Documented.__doc__)
''')

case("obj_super_explicit_arguments", "the two-argument super() form works", r'''
class Base:
    def tag(self):
        return "base"


class Child(Base):
    def tag(self):
        return "child+" + super(Child, self).tag()


print(Child().tag())
''')

case("obj_new_runs_before_init", "__new__ runs before __init__", r'''
class Ordered:
    def __new__(cls):
        print("new")
        return super().__new__(cls)

    def __init__(self):
        print("init")


Ordered()
''')

case("obj_ne_derived_from_eq", "__ne__ defaults to the negation of __eq__", r'''
class Version:
    def __init__(self, n):
        self.n = n

    def __eq__(self, other):
        return self.n == other.n


print(Version(1) != Version(2))
print(Version(1) != Version(1))
''')

case("obj_class_getitem", "__class_getitem__ makes a class subscriptable", r'''
class Container:
    def __class_getitem__(cls, item):
        return "Container[" + item.__name__ + "]"


print(Container[int])
''')

case("obj_instancecheck_on_metaclass", "__instancecheck__ overrides isinstance", r'''
class AlwaysMeta(type):
    def __instancecheck__(cls, obj):
        return True


class Anything(metaclass=AlwaysMeta):
    pass


print(isinstance("a string", Anything))
print(isinstance(42, Anything))
''')

case("obj_abc_register_virtual_subclass", "ABCMeta.register creates a virtual subclass", r'''
import abc


class Drawable(abc.ABC):
    pass


class Circle:
    pass


Drawable.register(Circle)
print(issubclass(Circle, Drawable))
print(isinstance(Circle(), Drawable))
''')

case("obj_dataclass_frozen_rejects_write", "a frozen dataclass refuses assignment", r'''
import dataclasses


@dataclasses.dataclass(frozen=True)
class Point:
    x: int


p = Point(1)
print(p.x)
try:
    p.x = 2
    print("assignment allowed")
except dataclasses.FrozenInstanceError:
    print("assignment refused")
''')

case("obj_dataclass_order_comparisons", "dataclass(order=True) derives comparisons", r'''
import dataclasses


@dataclasses.dataclass(order=True)
class Version:
    major: int
    minor: int


print(Version(1, 2) < Version(1, 3))
print(sorted([Version(2, 0), Version(1, 5)])[0].major)
''')

case("obj_typing_namedtuple_class_syntax", "typing.NamedTuple's class form works", r'''
import typing


class Point(typing.NamedTuple):
    x: int
    y: int = 0


p = Point(1)
print(p.x)
print(p.y)
print(tuple(p))
''')

case("obj_enum_custom_method", "an Enum may define its own methods", r'''
import enum


class Color(enum.Enum):
    RED = 1
    GREEN = 2

    def describe(self):
        return self.name.lower() + "=" + str(self.value)


print(Color.RED.describe())
''')

case("obj_slots_with_property", "a property coexists with __slots__", r'''
class Point:
    __slots__ = ("_x",)

    def __init__(self, x):
        self._x = x

    @property
    def x(self):
        return self._x


print(Point(4).x)
''')

case("obj_int_dunder_conversion", "__int__ and __float__ serve the conversions", r'''
class Measure:
    def __init__(self, n):
        self.n = n

    def __int__(self):
        return int(self.n)

    def __float__(self):
        return float(self.n)


print(int(Measure(3.7)))
print(float(Measure(3)))
''')

case("obj_bytes_dunder", "__bytes__ serves bytes()", r'''
class Blob:
    def __bytes__(self):
        return b"payload"


print(bytes(Blob()))
''')

case("obj_round_dunder", "__round__ serves round()", r'''
class Measure:
    def __init__(self, n):
        self.n = n

    def __round__(self, digits=None):
        return "rounded"


print(round(Measure(1.23)))
''')

case("obj_copy_dunder", "__copy__ customises copy.copy", r'''
import copy


class Tagged:
    def __copy__(self):
        clone = Tagged()
        clone.tag = "copied"
        return clone


print(copy.copy(Tagged()).tag)
''')

case("obj_delattr_removes", "delattr removes an instance attribute", r'''
class Holder:
    def __init__(self):
        self.field = 1


h = Holder()
print(hasattr(h, "field"))
delattr(h, "field")
print(hasattr(h, "field"))
''')

case("obj_staticmethod_inherited", "a subclass inherits a staticmethod", r'''
class Base:
    @staticmethod
    def helper():
        return "helper"


class Child(Base):
    pass


print(Child.helper())
print(Child().helper())
''')

case("obj_cooperative_init_chain", "each __init__ in a diamond runs once", r'''
class Base:
    def __init__(self):
        self.trail = ["Base"]


class Left(Base):
    def __init__(self):
        super().__init__()
        self.trail.append("Left")


class Right(Base):
    def __init__(self):
        super().__init__()
        self.trail.append("Right")


class Both(Left, Right):
    def __init__(self):
        super().__init__()
        self.trail.append("Both")


print(Both().trail)
''')

case("obj_classvar_list_is_shared", "a mutable class attribute is shared", r'''
class Registry:
    entries = []

    def add(self, value):
        self.entries.append(value)


Registry().add("a")
Registry().add("b")
print(Registry.entries)
''')

case("obj_method_is_class_attribute", "a method is reachable through the class", r'''
class Greeter:
    def greet(self):
        return "hi"


g = Greeter()
print(Greeter.greet(g))
print(g.greet())
''')

case("obj_callable_reports_instances", "callable() reflects __call__", r'''
class WithCall:
    def __call__(self):
        return 1


class WithoutCall:
    pass


print(callable(WithCall()))
print(callable(WithoutCall()))
print(callable(WithoutCall))
''')

case("obj_dir_includes_attributes", "dir() lists declared members", r'''
class Widget:
    field = 1

    def method(self):
        return 2


names = dir(Widget())
print("field" in names)
print("method" in names)
print("absent" in names)
''')

case("obj_inherited_property_override", "a subclass can override an inherited property", r'''
class Base:
    @property
    def label(self):
        return "base"


class Child(Base):
    @property
    def label(self):
        return "child"


print(Base().label)
print(Child().label)
''')


if __name__ == "__main__":
    raise SystemExit(main(CASES, "gen_obj_cases.py", sys.argv))
