# expect:
# int
# float
# int
for x in [1, 2.0, 3]:
    print(type(x).__name__)
# asmpython (beta/3.14.0) rejects at compile: [E051] mixed list element types (int and float); mixed-type lists need a tagged-value runtime, not yet implemented
