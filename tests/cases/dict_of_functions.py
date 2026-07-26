# expect:
# 7
handlers = {'add': lambda a, b: a + b, 'sub': lambda a, b: a - b}
print(handlers['add'](3, 4))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method '__call__'
