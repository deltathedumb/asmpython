# probes: a generator expression is consumed lazily
# expect:
# 14
squares = (n * n for n in [1, 2, 3])
print(sum(squares))
