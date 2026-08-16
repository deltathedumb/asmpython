# expect:
# 11
def outer(callback):
    def inner(x):
        return callback(x) + 1
    return inner
fn = outer(lambda x: x * 2)
print(fn(5))
# asmpython (beta/3.14.0) rejects at compile: [E002] undefined function 'callback'
