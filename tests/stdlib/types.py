# COVERAGE: ModuleType, SimpleNamespace, new_class. NOT covered: FunctionType,
# MethodType, GeneratorType, MappingProxyType, prepare_class -- see the module.
import types

ns = types.SimpleNamespace(a=1, b="two")
print(ns.a, ns.b)
print(ns)
ns.c = 3.5
print(ns.c, ns == types.SimpleNamespace(a=1, b="two", c=3.5))
print(types.SimpleNamespace() == types.SimpleNamespace())
print(types.SimpleNamespace(x=1) == types.SimpleNamespace(x=2))

m = types.ModuleType("made_up")
print(m.__name__)
m.value = 7
print(m.value)

C = types.new_class("C", (), {}, lambda ns: ns.update({"n": 1}))
print(C.__name__, C().n)


class Base:
    def who(self):
        return "base"


D = types.new_class("D", (Base,), {}, lambda ns: ns.update({"who": lambda s: "derived"}))
print(D.__name__, D().who(), issubclass(D, Base))
