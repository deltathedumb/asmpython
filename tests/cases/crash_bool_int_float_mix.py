# expect:
# 4.5
vals = [True, 1, 2.5]
print(sum(vals))
# asmpython (beta/3.14.0) rejects at compile: [E051] mixed list element types (int and float); mixed-type lists need a tagged-value runtime, not yet implemented
