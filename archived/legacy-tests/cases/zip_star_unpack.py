# expect:
# (1, 2) ('a', 'b')
pairs = [(1, 'a'), (2, 'b')]
nums, lets = zip(*pairs)
print(nums, lets)
# asmpython (beta/3.14.0) rejects at compile: [E023] *expr argument unpacking requires a tuple with known element types
