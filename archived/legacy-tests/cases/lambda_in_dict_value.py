# expect:
# 16
handlers = {'sq': lambda x: x * x}
print(handlers['sq'](4))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method '__call__'
