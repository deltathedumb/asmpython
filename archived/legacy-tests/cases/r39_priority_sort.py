# expect:
# ['b', 'a', 'c']
tasks = [{'name': 'a', 'pri': 2}, {'name': 'b', 'pri': 1}, {'name': 'c', 'pri': 3}]
ordered = sorted(tasks, key=lambda t: t['pri'])
print([t['name'] for t in ordered])
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (sorted key lambda body)
