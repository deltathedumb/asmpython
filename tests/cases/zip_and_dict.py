# expect:
# alice 30
keys = ['name', 'age', 'city']
values = ['alice', 30, 'nyc']
record = dict(zip(keys, values))
print(record['name'], record['age'])
# asmpython (beta/3.14.0) rejects at compile: [E051] mixed list element types (str and int); mixed-type lists need a tagged-value runtime, not yet implemented
