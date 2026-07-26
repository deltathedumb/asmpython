# expect:
# [(0, 'c'), (1, 'b'), (2, 'a')]
print(list(enumerate(reversed(['a', 'b', 'c']))))
# asmpython (beta/3.14.0) rejects at compile: [E002] undefined function 'enumerate'
