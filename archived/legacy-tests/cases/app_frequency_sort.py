# expect:
# [('a', 3), ('b', 2), ('c', 1)]
text = 'aaabbc'
freq = {}
for c in text:
    freq[c] = freq.get(c, 0) + 1
ordered = sorted(freq.items(), key=lambda x: -x[1])
print(ordered)
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (sorted key lambda body)
