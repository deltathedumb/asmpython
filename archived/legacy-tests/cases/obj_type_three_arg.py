# probes: type(name, bases, dict) builds a class
# expect:
# Dynamic
# hi
Dynamic = type("Dynamic", (), {"greet": lambda self: "hi"})
d = Dynamic()
print(Dynamic.__name__)
print(d.greet())
