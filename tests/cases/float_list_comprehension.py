# expect:
# [1.0, 2.0, 3.0]
print([x / 2 for x in [2, 4, 6]])
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Comprehension (float element)
