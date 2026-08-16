# expect:
# [1.2, 5.7, 9.0]
vals = [1.234, 5.678, 9.012]
print([round(v, 1) for v in vals])
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Comprehension (float element)
