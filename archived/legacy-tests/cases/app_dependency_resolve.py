# expect:
# ['d', 'b', 'c', 'a']
deps = {'a': ['b', 'c'], 'b': ['d'], 'c': ['d'], 'd': []}
def resolve(node, seen):
    if node in seen:
        return
    for dep in deps[node]:
        resolve(dep, seen)
    seen.append(node)
order = []
resolve('a', order)
print(order)
# asmpython (beta/3.14.0) MISMATCH: prints '[]\n' (wrong).
