# expect:
# 1+2+3
print(*[1, 2, 3], sep='+')
# asmpython (beta/3.14.0) rejects at compile: [E023] *expr argument unpacking requires a tuple with known element types
