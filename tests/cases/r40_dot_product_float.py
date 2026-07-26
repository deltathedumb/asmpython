# expect:
# 14.5
a = [1.5, 2.5]
b = [3.0, 4.0]
result = sum(a[i] * b[i] for i in range(2))
print(result)
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Comprehension (float element)
