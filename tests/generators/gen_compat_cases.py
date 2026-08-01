"""Generate patch-layer regression cases for asmpython Phase 0.

Each `*_compat_fixes.py` module in asmpython/_compiler encodes a conformance
requirement that today is satisfied ONLY by a monkeypatch. When those modules
are deleted during a core rebuild, the requirement must still hold. This script
emits one minimal test case per requirement, with the `# expect:` block taken
from real CPython 3.14 output -- never hand-written.

Usage: python gen_compat_cases.py <tests/cases dir>
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# name -> (compat module it guards, source)
CASES: dict[str, tuple[str, str]] = {}


def case(name: str, guards: str, src: str) -> None:
    CASES[name] = (guards, src.strip() + "\n")


# --------------------------------------------------------------------------
# analysis_compat_fixes: unannotated returns are dynamic, not int; unreachable
# bodies must not block compilation.
# --------------------------------------------------------------------------
case("compat_analysis_dynamic_return", "analysis_compat_fixes", '''
def describe(value):
    return "value=" + str(value)


def unused_helper(payload):
    # Never called by the entry graph. Must not block compilation.
    return payload.missing_attribute_on_purpose()


print(describe(7))
print(describe("x"))
''')

# --------------------------------------------------------------------------
# boolop_value_compat_fixes: and/or return an OPERAND, not a coerced bool.
# --------------------------------------------------------------------------
case("compat_boolop_operand_value", "boolop_value_compat_fixes", '''
def pick(primary, fallback):
    return primary or fallback


print(pick("", "default"))
print(pick("set", "default"))
print(0 or 5)
print(3 and 4)
print("" or "empty-wins")
name = "" or "anon"
print(name)
''')

# --------------------------------------------------------------------------
# chained_receiver_compat_fixes: obj.property.method()
# --------------------------------------------------------------------------
case("compat_chained_receiver", "chained_receiver_compat_fixes", '''
class Inner:
    def __init__(self, tag):
        self.tag = tag

    def label(self):
        return "inner:" + self.tag


class Outer:
    def __init__(self):
        self._inner = Inner("a")

    @property
    def inner(self):
        return self._inner


o = Outer()
print(o.inner.label())
print(o.inner.tag)
''')

# --------------------------------------------------------------------------
# class_registry_compat_fixes: classes stored in a dict, resolved later.
# --------------------------------------------------------------------------
case("compat_class_registry", "class_registry_compat_fixes", '''
class Registry:
    def __init__(self):
        self._types = {}

    def register(self, name, cls):
        self._types[name] = cls
        return cls

    def create(self, name, value):
        cls = self._types.get(name)
        if cls is None:
            return None
        return cls(value)


class Widget:
    def __init__(self, value):
        self.value = value

    def show(self):
        return "widget " + str(self.value)


REGISTRY = Registry()
REGISTRY.register("widget", Widget)
made = REGISTRY.create("widget", 12)
print(made.show())
print(REGISTRY.create("missing", 1) is None)
''')

# --------------------------------------------------------------------------
# class_string_compat_fixes: accept either a class object or a string name.
# --------------------------------------------------------------------------
case("compat_class_string", "class_string_compat_fixes", '''
class Alpha:
    pass


def name_of(value):
    if isinstance(value, type):
        return value.__name__
    return str(value)


print(name_of(Alpha))
print(name_of("beta"))
''')

# --------------------------------------------------------------------------
# class_value_compat_fixes: literal tuple of classes, indexed and iterated.
# --------------------------------------------------------------------------
case("compat_class_value_tuple", "class_value_compat_fixes", '''
class A:
    def tag(self):
        return "A"


class B:
    def tag(self):
        return "B"


KINDS = (A, B)

first = KINDS[0]
print(first().tag())

for kind in KINDS:
    print(kind().tag())
''')

# --------------------------------------------------------------------------
# container_field_compat_fixes: collection metadata through reads and copies.
# --------------------------------------------------------------------------
case("compat_container_field_copy", "container_field_compat_fixes", '''
class Holder:
    def __init__(self):
        self.items = [1, 2, 3]

    def copy_items(self):
        return list(self.items)


h = Holder()
copied = h.copy_items()
copied.append(4)
print(len(h.items))
print(len(copied))
total = 0
for v in h.items:
    total = total + v
print(total)
''')

# --------------------------------------------------------------------------
# descriptor_precedence_compat_fixes: data descriptors beat the instance dict.
# --------------------------------------------------------------------------
case("compat_descriptor_precedence", "descriptor_precedence_compat_fixes", '''
class Doubling:
    def __init__(self):
        self._store = {}

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return self._store.get(id(obj), 0)

    def __set__(self, obj, value):
        self._store[id(obj)] = value * 2


class Thing:
    amount = Doubling()


t = Thing()
t.amount = 5
print(t.amount)
t.amount = 10
print(t.amount)
''')

# --------------------------------------------------------------------------
# dynamic_classvar_compat_fixes: cls.<var> on a classmethod receiver, with
# inheritance and overrides.
# --------------------------------------------------------------------------
case("compat_dynamic_classvar", "dynamic_classvar_compat_fixes", '''
class Base:
    label = "base"

    @classmethod
    def describe(cls):
        return "label=" + cls.label


class Child(Base):
    label = "child"


class Grandchild(Child):
    pass


print(Base.describe())
print(Child.describe())
print(Grandchild.describe())
''')

# --------------------------------------------------------------------------
# dynamic_index_assignment_compat_fixes: obj[name] = value with a string key.
# --------------------------------------------------------------------------
case("compat_dynamic_index_assign", "dynamic_index_assignment_compat_fixes", '''
def store(target, key, value):
    target[key] = value
    return target[key]


bag = {}
print(store(bag, "alpha", 1))
print(store(bag, "beta", 2))
print(len(bag))
''')

# --------------------------------------------------------------------------
# dynamic_parameter_compat_fixes: unannotated param used as a non-int.
# --------------------------------------------------------------------------
case("compat_dynamic_parameter", "dynamic_parameter_compat_fixes", '''
def join_all(parts):
    out = ""
    for p in parts:
        out = out + p
    return out


def call_it(fn, arg):
    return fn(arg)


def shout(text):
    return text.upper()


print(join_all(["a", "b", "c"]))
print(call_it(shout, "hi"))
''')

# --------------------------------------------------------------------------
# empty_collection_compat_fixes: empty collection field never mutated.
# --------------------------------------------------------------------------
case("compat_empty_collection_field", "empty_collection_compat_fixes", '''
class Emitter:
    def __init__(self):
        self.listeners = []

    def fire(self):
        count = 0
        for listener in self.listeners:
            count = count + 1
        return count


e = Emitter()
print(e.fire())
print(len(e.listeners))
''')

# --------------------------------------------------------------------------
# field_flow_compat_fixes: constructor parameter and field types inferred from
# whole-program call sites.
# --------------------------------------------------------------------------
case("compat_field_flow_ctor", "field_flow_compat_fixes", '''
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def render(self):
        return str(self.x) + "," + str(self.y)


class Named:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return "hello " + self.name


print(Point(3, 4).render())
print(Named("ada").greet())
''')

# --------------------------------------------------------------------------
# global_return_flow_compat_fixes: module globals visible to return inference.
# --------------------------------------------------------------------------
case("compat_global_return_flow", "global_return_flow_compat_fixes", '''
class Catalog:
    def __init__(self):
        self.prefix = "item-"

    def label(self, n):
        return self.prefix + str(n)


CATALOG = Catalog()


def make_label(n):
    return CATALOG.label(n)


print(make_label(1))
print(make_label(42))
''')

# --------------------------------------------------------------------------
# inherited_classmethod_compat_fixes: Child.method() resolves to the defining
# owner, but cls binds to the concrete call-site class.
# --------------------------------------------------------------------------
case("compat_inherited_classmethod", "inherited_classmethod_compat_fixes", '''
class Provider:
    kind = "provider"

    @classmethod
    def supports(cls, what):
        return cls.kind + ":" + what

    @staticmethod
    def version():
        return 2


class Scene(Provider):
    kind = "scene"


print(Provider.supports("a"))
print(Scene.supports("b"))
print(Scene.version())
''')

# --------------------------------------------------------------------------
# issubclass_compat_fixes: issubclass against named classes and tuples.
# --------------------------------------------------------------------------
case("compat_issubclass", "issubclass_compat_fixes", '''
class Root:
    pass


class Mid(Root):
    pass


class Leaf(Mid):
    pass


class Other:
    pass


print(issubclass(Leaf, Root))
print(issubclass(Mid, Root))
print(issubclass(Root, Leaf))
print(issubclass(Other, Root))
print(issubclass(Leaf, (Other, Root)))
print(issubclass(Other, (Root, Mid)))
''')

# --------------------------------------------------------------------------
# iter_next_compat_fixes: explicit iter()/next() as real builtins.
# --------------------------------------------------------------------------
case("compat_iter_next_builtins", "iter_next_compat_fixes", '''
class Counter:
    def __init__(self, limit):
        self.limit = limit
        self.n = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.n >= self.limit:
            raise StopIteration
        self.n = self.n + 1
        return self.n


it = iter([10, 20, 30])
print(next(it))
print(next(it))

si = iter("ab")
print(next(si))

ci = iter(Counter(2))
print(next(ci))
print(next(ci))

total = 0
for v in Counter(3):
    total = total + v
print(total)
''')

# --------------------------------------------------------------------------
# iterable_element_compat_fixes: element types survive helpers and generators.
# --------------------------------------------------------------------------
case("compat_iterable_element_helper", "iterable_element_compat_fixes", '''
def first_of(seq):
    for item in seq:
        return item
    return None


def names():
    return ["ada", "bob"]


picked = first_of(names())
print(picked.upper())

nums = [3, 1, 2]
print(first_of(nums) + 1)
''')

# --------------------------------------------------------------------------
# language_compat_fixes: yield from.
# --------------------------------------------------------------------------
case("compat_language_yield_from", "language_compat_fixes", '''
def inner():
    yield 1
    yield 2


def outer():
    yield 0
    yield from inner()
    yield 3


for v in outer():
    print(v)
''')

# --------------------------------------------------------------------------
# language_compat_fixes: calls on an arbitrary primary expression.
# --------------------------------------------------------------------------
case("compat_language_expression_call", "language_compat_fixes", '''
class Adder:
    def __init__(self, base):
        self.base = base

    def __call__(self, n):
        return self.base + n


def get_adder(base):
    return Adder(base)


print(get_adder(10)(5))

table = {"a": Adder(1)}
print(table["a"](2))
''')

# --------------------------------------------------------------------------
# language_compat_fixes: method with a non-`self` receiver name.
# --------------------------------------------------------------------------
case("compat_language_receiver_name", "language_compat_fixes", '''
class Box:
    def __init__(this, value):
        this.value = value

    def show(this):
        return "box(" + str(this.value) + ")"

    def bump(obj):
        obj.value = obj.value + 1
        return obj.value


b = Box(1)
print(b.show())
print(b.bump())
print(b.show())
''')

# --------------------------------------------------------------------------
# live_definition_compat_fixes: re-exported but never-dispatched bodies.
# --------------------------------------------------------------------------
case("compat_live_definition_unused", "live_definition_compat_fixes", '''
class Used:
    def run(self):
        return "ran"


class NeverConstructed:
    def broken(self):
        return self.does_not_exist.at_all()


print(Used().run())
''')

# --------------------------------------------------------------------------
# metaclass_compat_fixes: descriptor-collecting metaclass.
# --------------------------------------------------------------------------
case("compat_metaclass_descriptor_collect", "metaclass_compat_fixes", '''
class Field:
    def __init__(self, kind):
        self.kind = kind


class Meta(type):
    def __new__(mcls, name, bases, ns):
        collected = {}
        for key in ns:
            value = ns[key]
            if isinstance(value, Field):
                collected[key] = value.kind
        cls = super().__new__(mcls, name, bases, ns)
        cls._fields = collected
        return cls


class Model(metaclass=Meta):
    ident = Field("int")
    title = Field("str")

    @classmethod
    def fields(cls):
        return dict(cls._fields)


f = Model.fields()
print(len(f))
print(f["ident"])
print(f["title"])
''')

# --------------------------------------------------------------------------
# object_flow_compat_fixes: generator methods on an instance.
# --------------------------------------------------------------------------
case("compat_object_flow_generator", "object_flow_compat_fixes", '''
class Tree:
    def __init__(self, values):
        self.values = values

    def walk(self):
        for v in self.values:
            yield v * 2


t = Tree([1, 2, 3])
for v in t.walk():
    print(v)
''')

# --------------------------------------------------------------------------
# object_flow_compat_fixes: return class selected by a class-object parameter.
# --------------------------------------------------------------------------
case("compat_object_flow_class_param", "object_flow_compat_fixes", '''
class World:
    def name(self):
        return "world"


class Engine:
    def name(self):
        return "engine"


class Services:
    def __init__(self):
        self._made = {}

    def get_service(self, kind):
        return kind()


s = Services()
print(s.get_service(World).name())
print(s.get_service(Engine).name())
''')

# --------------------------------------------------------------------------
# return_annotation_precedence_compat_fixes: explicit annotation is a contract.
# --------------------------------------------------------------------------
case("compat_return_annotation_precedence", "return_annotation_precedence_compat_fixes", '''
class Vec3:
    def __init__(self, x):
        self.x = x

    def show(self):
        return "vec" + str(self.x)


class Owner:
    def __init__(self):
        self._v = Vec3(1)

    @property
    def position(self) -> Vec3:
        return self._v


o = Owner()
print(o.position.show())
''')

# --------------------------------------------------------------------------
# type_parameter_compat_fixes: finite type-valued parameter specialization.
# --------------------------------------------------------------------------
case("compat_type_parameter_specialize", "type_parameter_compat_fixes", '''
class World:
    def tag(self):
        return "world"


class Engine:
    def tag(self):
        return "engine"


class Container:
    def __init__(self):
        self.have = {}

    def ensure(self, service_type):
        found = self.have.get(service_type.__name__)
        if found is None:
            found = service_type()
            self.have[service_type.__name__] = found
        return found


c = Container()
print(c.ensure(World).tag())
print(c.ensure(Engine).tag())
print(c.ensure(World).tag())
''')

# --------------------------------------------------------------------------
# ordered_flow_compat_fixes: several inference passes must interact in
# dependency order -- fields, returns, and constructor params at once.
# --------------------------------------------------------------------------
case("compat_ordered_flow_combined", "ordered_flow_compat_fixes", '''
class Leaf:
    def __init__(self, tag):
        self.tag = tag

    def show(self):
        return "leaf:" + self.tag


class Branch:
    def __init__(self):
        self.leaves = []

    def add(self, leaf):
        self.leaves.append(leaf)
        return self

    def newest(self):
        return self.leaves[len(self.leaves) - 1]


b = Branch()
b.add(Leaf("a")).add(Leaf("b"))
print(b.newest().show())
print(len(b.leaves))
''')

# --------------------------------------------------------------------------
# language_compat_fixes: type(name)(...) resolving to a known constructor.
# --------------------------------------------------------------------------
case("compat_language_type_constructor", "language_compat_fixes", '''
class Sample:
    def __init__(self):
        self.tag = "sample"

    def show(self):
        return self.tag


proto = Sample()
clone = type(proto)()
print(clone.show())
print(type(proto).__name__)
''')


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: gen_compat_cases.py <tests/cases dir>", file=sys.stderr)
        return 2
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = []
    for name, (guards, src) in sorted(CASES.items()):
        tmp = out_dir.parent / f"_gen_{name}.py"
        tmp.write_text(src, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(tmp)],
                capture_output=True, text=True, timeout=30,
            )
        finally:
            tmp.unlink(missing_ok=True)

        if proc.returncode != 0:
            skipped.append((name, proc.stderr.strip().splitlines()[-1:] or ["?"]))
            continue

        lines = proc.stdout.splitlines()
        # `guards:` MUST precede `# expect:` -- _parse_expect() collects every
        # `#` line after the marker into expected stdout, so a trailing marker
        # would be read as an extra expected output line.
        header = [f"# guards: {guards}", "# expect:"]
        header += [f"# {ln}" if ln else "#" for ln in lines]
        (out_dir / f"{name}.py").write_text(
            "\n".join(header) + "\n" + src, encoding="utf-8"
        )
        written += 1

    print(f"wrote {written} cases to {out_dir}")
    for name, err in skipped:
        print(f"  SKIPPED {name}: {err}")
    return 0 if not skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
