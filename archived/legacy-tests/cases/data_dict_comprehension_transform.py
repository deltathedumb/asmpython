# expect:
# [('a', 90.0), ('b', 180.0)]
prices = {'a': 100, 'b': 200}
discounted = {k: v * 0.9 for k, v in prices.items()}
print(sorted(discounted.items()))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr DictComprehension (float value)
