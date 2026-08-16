# expect:
# 0 x
# 1 y
for i, k in enumerate({'x': 1, 'y': 2}):
    print(i, k)
# asmpython (beta/3.14.0) rejects at compile: unsupported stmt For (enumerate 'dict')
