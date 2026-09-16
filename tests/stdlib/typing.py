# COVERAGE: TypeVar (construction, __name__, __bound__, __constraints__,
# repr); Generic with a real __class_getitem__ (class Box(Generic[T]) then
# Box[int] answers an object with real __origin__/__args__); NewType and
# cast as real passthroughs; overload as a real (dispatch-free) decorator;
# Protocol + runtime_checkable with structural isinstance; get_type_hints on
# a function and on a class; NamedTuple and TypedDict, FUNCTIONAL FORM ONLY
# (the class-based form is refused -- see bundled/typing.py). NOT covered:
# get_origin/get_args on a user Generic[T] subscript, class-based
# NamedTuple/TypedDict, attribute-only Protocol members -- each documented
# in bundled/typing.py.
import typing


def show(label, value):
    print(label, "=", value)


# ── TypeVar ──────────────────────────────────────────────────────────────

T = typing.TypeVar("T")
show("T", T)
show("T.__name__", T.__name__)
show("T.__bound__", T.__bound__)
show("T.__constraints__", T.__constraints__)

U = typing.TypeVar("U", bound=int)
show("U.__bound__", U.__bound__)

V = typing.TypeVar("V", int, str)
show("V.__constraints__", V.__constraints__)

Co = typing.TypeVar("Co", covariant=True)
show("Co repr", Co)


# ── Generic ──────────────────────────────────────────────────────────────

class Box(typing.Generic[T]):
    def __init__(self, item):
        self.item = item


b = Box[int]
# NOT a raw class repr: uasm's user-class repr omits the module
# qualifier CPython includes (`<class 'Box'>` vs `<class '__main__.Box'>`),
# a pre-existing, unrelated difference -- so this checks identity instead.
show("Box[int].__origin__ is Box", b.__origin__ is Box)
show("Box[int].__args__", b.__args__)

inst = Box(5)
show("Box(5).item", inst.item)

# The pre-existing native path -- unchanged by this module.
show("get_origin(list[int])", typing.get_origin(list[int]))
show("get_args(list[int])", typing.get_args(list[int]))


# ── NewType, cast ────────────────────────────────────────────────────────

UserId = typing.NewType("UserId", int)
uid = UserId(5)
show("UserId(5)", uid)
show("type(UserId(5))", type(uid))
show("UserId.__name__", UserId.__name__)
show("UserId.__supertype__", UserId.__supertype__)

show("cast(int, 'x')", typing.cast(int, "x"))
show("cast(str, 5)", typing.cast(str, 5))


# ── overload ─────────────────────────────────────────────────────────────

@typing.overload
def combine(x, y): ...
@typing.overload
def combine(x, y): ...
def combine(x, y):
    return x + y


show("combine(1, 2)", combine(1, 2))
show("combine('a', 'b')", combine("a", "b"))

try:
    stub = typing.overload(lambda x: x)
    stub(1)
except NotImplementedError as e:
    print("overload stub raised NotImplementedError")


# ── Protocol ─────────────────────────────────────────────────────────────

class HasArea(typing.Protocol):
    def area(self) -> float: ...


class Circle:
    def __init__(self, r):
        self.r = r

    def area(self):
        return 3.0 * self.r * self.r


try:
    isinstance(Circle(2), HasArea)
except TypeError as e:
    print("non-runtime-checkable Protocol raised TypeError")


@typing.runtime_checkable
class HasAreaRC(typing.Protocol):
    def area(self) -> float: ...


show("isinstance(Circle(2), HasAreaRC)", isinstance(Circle(2), HasAreaRC))
show("isinstance(3, HasAreaRC)", isinstance(3, HasAreaRC))
show("isinstance('x', HasAreaRC)", isinstance("x", HasAreaRC))


# ── get_type_hints ───────────────────────────────────────────────────────

def annotated(x: int, y: str = "a") -> bool:
    return True


show("get_type_hints(annotated)", typing.get_type_hints(annotated))


class Config:
    name: str
    value: int


show("get_type_hints(Config)", typing.get_type_hints(Config))


# ── NamedTuple, functional form ──────────────────────────────────────────

Point = typing.NamedTuple("Point", [("x", int), ("y", int)])
p = Point(1, 2)
show("Point(1, 2)", p)
show("p.x", p.x)
show("p.y", p.y)
show("p._fields", p._fields)
show("isinstance(p, tuple)", isinstance(p, tuple))
show("Point.__annotations__", Point.__annotations__)


# ── TypedDict, functional form ───────────────────────────────────────────

Movie = typing.TypedDict("Movie", {"title": str, "year": int})
m = Movie(title="Arrival", year=2016)
show("Movie(...)", m)
show("type(Movie(...))", type(m))
show("Movie.__required_keys__", sorted(Movie.__required_keys__))
show("Movie.__optional_keys__", sorted(Movie.__optional_keys__))
show("Movie.__annotations__", Movie.__annotations__)

PartialMovie = typing.TypedDict("PartialMovie", {"title": str}, total=False)
show("PartialMovie.__required_keys__", sorted(PartialMovie.__required_keys__))
show("PartialMovie.__optional_keys__", sorted(PartialMovie.__optional_keys__))

print("done")

# ---- ParamSpec and TypeVarTuple -------------------------------------------
P = typing.ParamSpec("P")
print(P.__name__, repr(P))
print(P.args, P.kwargs, P.args.__origin__ is P)
print(typing.get_args(typing.Callable[P, int])[1] is int)

Ts = typing.TypeVarTuple("Ts")
print(Ts.__name__, repr(Ts))


class Arr(typing.Generic[typing.Unpack[Ts]]):
    pass


print(Arr[int, str].__args__)

# ---- get_origin/get_args reach a user generic too --------------------------
T2 = typing.TypeVar("T2")


class Box2(typing.Generic[T2]):
    pass


print(typing.get_origin(Box2[int]) is Box2, typing.get_args(Box2[int]))
print(typing.get_origin(3), typing.get_args(3))
print(typing.get_origin(list[int]), typing.get_args(dict[str, int]))

# ---- Annotated: stripped by default, kept on request ----------------------
Ann = typing.Annotated


class Held:
    a: Ann[int, "m"]
    b: list[Ann[int, "n"]]
    c: typing.Optional[Ann[str, "o"]]


for key in ("a", "b", "c"):
    print(key, typing.get_type_hints(Held)[key],
          "|", typing.get_type_hints(Held, include_extras=True)[key])

# ---- dataclass_transform is inert, and says so ----------------------------
@typing.dataclass_transform(order_default=True)
def model(cls):
    return cls


@model
class Marked:
    x: int


print(Marked.__name__, model.__dataclass_transform__["eq_default"],
      model.__dataclass_transform__["order_default"],
      model.__dataclass_transform__["field_specifiers"])

# ---- TypedDict, both forms ------------------------------------------------
class Movie(typing.TypedDict):
    name: str
    year: int


class Config(typing.TypedDict):
    name: typing.Required[str]
    debug: typing.NotRequired[bool]


class Owned(Config):
    fixed: typing.ReadOnly[int]


class Loose(typing.TypedDict, total=False):
    x: int
    y: typing.Required[str]


class Nested(typing.TypedDict):
    k: typing.Required[typing.ReadOnly[int]]


Made = typing.TypedDict("Made", {"m": int, "n": typing.NotRequired[str]})
Partial = typing.TypedDict("Partial", {"p": int}, total=False)

for one in (Movie, Config, Owned, Loose, Nested, Made, Partial):
    print(one.__name__, one.__total__,
          sorted(one.__required_keys__), sorted(one.__optional_keys__),
          sorted(one.__readonly_keys__), sorted(one.__mutable_keys__))
print(sorted(Movie.__annotations__), sorted(Made.__annotations__))
print(Movie(name="x", year=2000), type(Movie(name="x")).__name__)
print(Made(m=1, n="two"), Partial())
m: Movie = {"name": "x", "year": 2000}
print(sorted(m.items()), type(m).__name__)


def takes(**kw: typing.Unpack[Movie]):
    return sorted(kw.items())


print(takes(name="a", year=1))
