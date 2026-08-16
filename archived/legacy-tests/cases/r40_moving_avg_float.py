# expect:
# [1.5, 2.5, 3.5, 4.5]
data = [1.0, 2.0, 3.0, 4.0, 5.0]
window = 2
result = [(data[i] + data[i + 1]) / 2 for i in range(len(data) - 1)]
print(result)
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Comprehension (float element)
