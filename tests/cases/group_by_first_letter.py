# expect:
# a ['apple', 'avocado']
# b ['banana']
# c ['cherry']
words = ['apple', 'avocado', 'banana', 'cherry']
groups = {}
for w in words:
    groups.setdefault(w[0], []).append(w)
for k in sorted(groups):
    print(k, groups[k])
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'append'
