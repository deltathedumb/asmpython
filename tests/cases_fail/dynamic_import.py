# expect-error: importlib.import_module() is not supported
import importlib

mod = importlib.import_module("math")
print(mod)
