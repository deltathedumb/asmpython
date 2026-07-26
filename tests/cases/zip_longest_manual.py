# expect:
# [(1, 'x'), (2, 'y'), (3, None)]
a = [1, 2, 3]
b = ['x', 'y']
n = max(len(a), len(b))
out = [(a[i] if i < len(a) else None, b[i] if i < len(b) else None) for i in range(n)]
print(out)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
