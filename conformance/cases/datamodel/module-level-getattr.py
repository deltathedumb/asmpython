# tier: spec
# ref: reference/import.html#module-getattr
# expect:
# dynamic:anything
# module
import types

m = types.ModuleType("m")
m.__getattr__ = lambda name: "dynamic:" + name
print(m.__getattr__("anything"))
print(type(m).__name__)
