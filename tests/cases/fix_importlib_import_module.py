# probes: importlib.import_module loads by name
# expect:
# [1, 2]
# json
import importlib

module = importlib.import_module("json")
print(module.dumps([1, 2]))
print(module.__name__)
