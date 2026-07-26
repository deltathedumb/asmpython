# expect:
# [10.0, 15.0, 20.0, 25.0]
values = [10, 20, 30, 40]
averages = []
for i in range(len(values)):
    window = values[:i + 1]
    averages.append(sum(window) / len(window))
print([round(a, 1) for a in averages])
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Comprehension (float element)
