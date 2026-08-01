# probes: isinstance accepts a tuple of types
# expect:
# True
# True
# False
print(isinstance(1, (str, int)))
print(isinstance("s", (str, int)))
print(isinstance(1.5, (str, int)))
