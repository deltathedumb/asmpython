# expect:
# 0 a
# 1 b
def gen():
    yield 'a'
    yield 'b'
for i, v in enumerate(gen()):
    print(i, v)
# asmpython (beta/3.14.0) rejects at compile: unsupported stmt YieldStmt
