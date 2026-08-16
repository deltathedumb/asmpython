# expect:
# 1 a
# 2 b
pairs = [[1, 'a'], [2, 'b']]
for n, c in pairs:
    print(n, c)
# asmpython (beta/3.14.0) rejects at compile: [E051] mixed list element types (int and str); mixed-type lists need a tagged-value runtime, not yet implemented
