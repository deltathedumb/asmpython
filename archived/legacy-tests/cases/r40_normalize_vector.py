# expect:
# [0.6, 0.8]
def normalize(v):
    mag = sum(x * x for x in v) ** 0.5
    return [x / mag for x in v]
print(normalize([3, 4]))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Comprehension (float element)
