# expect:
# ['A', 'C']
people = [{'name': 'C', 'age': 30}, {'name': 'A', 'age': 25}]
by_name = sorted(people, key=lambda p: p['name'])
print([p['name'] for p in by_name])
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (sorted key lambda body)
