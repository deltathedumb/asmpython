# expect:
# 3.0
import importlib

mod = importlib.import_module("math")
print(mod.sqrt(9))
