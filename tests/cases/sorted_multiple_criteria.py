# expect:
# ['alice', 'carol', 'bob']
people = [('alice', 30), ('bob', 25), ('carol', 30)]
s = sorted(people, key=lambda p: (-p[1], p[0]))
print([p[0] for p in s])
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (sorted key lambda body)
