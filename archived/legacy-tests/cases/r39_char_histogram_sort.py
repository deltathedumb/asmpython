# expect:
# i 4
# s 4
# p 2
# m 1
text = 'mississippi'
freq = {}
for c in text:
    freq[c] = freq.get(c, 0) + 1
for c, n in sorted(freq.items(), key=lambda x: (-x[1], x[0])):
    print(c, n)
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (sorted key lambda body)
