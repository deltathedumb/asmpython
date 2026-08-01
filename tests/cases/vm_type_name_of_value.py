# probes: type(v).__name__ reports the real kind
# expect:
# int
# str
# float
# bool
# list
print(type(1).__name__)
print(type("s").__name__)
print(type(1.5).__name__)
print(type(True).__name__)
print(type([1]).__name__)
