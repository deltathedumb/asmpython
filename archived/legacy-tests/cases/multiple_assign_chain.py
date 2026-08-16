# expect:
# 0 5 0
a = b = c = 0
b = 5
print(a, b, c)
# asmpython (beta/3.14.0) rejects at compile: unsupported stmt MultiAssign
