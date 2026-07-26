# expect:
# [10.0, 5.0, 3.3333333333333335]
n = 10
result = [n / i for i in range(1, 4)]
print(result)
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Comprehension (float element)
