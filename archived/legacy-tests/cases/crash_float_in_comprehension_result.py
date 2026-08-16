# expect:
# [1.5, 3.0, 4.5]
print([round(x * 1.5, 1) for x in range(1, 4)])
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Comprehension (float element)
