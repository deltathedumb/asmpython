# expect:
# int
# str
# list
# dict
for obj in [5, 'x', [1], {'a': 1}]:
    print(type(obj).__name__)
# asmpython (beta/3.14.0) rejects at compile: [E051] mixed list element types (int and str); mixed-type lists need a tagged-value runtime, not yet implemented
