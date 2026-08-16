# expect:
# [{'age': 20}, {'age': 30}]
data = [{'age': 30}, {'age': 20}]
print(sorted(data, key=lambda d: d['age']))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (sorted key lambda body)
