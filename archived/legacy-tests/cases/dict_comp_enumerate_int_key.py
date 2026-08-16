# expect:
# {0: 'a', 1: 'b', 2: 'c'}
print({i: c for i, c in enumerate('abc')})
# int keys from enumerate become str keys; asmpython prints {'0': 'a', ...}.
