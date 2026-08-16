# probes: repr renders nested containers
# expect:
# {'k': [1, (2, 3)], 's': {'inner': None}}
print(repr({"k": [1, (2, 3)], "s": {"inner": None}}))
