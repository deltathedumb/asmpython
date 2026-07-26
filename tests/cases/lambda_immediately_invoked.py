# expect:
# 6
print((lambda x: x + 1)(5))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method '__call__'
