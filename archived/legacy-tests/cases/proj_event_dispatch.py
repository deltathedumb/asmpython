# expect:
# 7
handlers = {}
def on(event, fn):
    handlers[event] = fn
def emit(event, *args):
    if event in handlers:
        return handlers[event](*args)
on('add', lambda a, b: a + b)
print(emit('add', 3, 4))
# asmpython (beta/3.14.0) rejects at compile: [E023] *expr argument unpacking requires a tuple with known element types
