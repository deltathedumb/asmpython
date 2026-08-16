# probes: dict key views support set operations
# expect:
# ['y']
# ['x', 'y', 'z']
a = {"x": 1, "y": 2}
b = {"y": 3, "z": 4}
print(sorted(a.keys() & b.keys()))
print(sorted(a.keys() | b.keys()))
