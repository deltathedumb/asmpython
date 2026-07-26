# expect:
# ['abc', 'def', 'xyz']
words = ['abc', 'xyz', 'def']
print(sorted(words, key=lambda w: w[-1]))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (sorted key lambda body)
