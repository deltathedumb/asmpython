# expect:
# 1.5
data = {'vals': [1.5, 2.5], 'total': 4.0}
print(data['vals'][0])
# asmpython (beta/3.14.0) rejects at compile: [E148] mixed dict value types (list and float); a float value can't share a dict with non-floats
