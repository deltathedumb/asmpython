# tier: spec
# ref: reference/import.html#import-related-module-attributes
# expect:
# __main__
# True
# True
# True
print(__name__)
print(type(__doc__).__name__ in ("NoneType", "str"))
print(isinstance(__file__, str))
print("__builtins__" in globals())
