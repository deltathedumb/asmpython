# expect:
# ['h1', 'h2']
callbacks = []
def register(fn):
    callbacks.append(fn)
    return fn
@register
def handler1():
    return 'h1'
@register
def handler2():
    return 'h2'
print([cb() for cb in callbacks])
# asmpython (beta/3.14.0) MISMATCH: prints '[5368741888, 5368741891]\n' (wrong).
