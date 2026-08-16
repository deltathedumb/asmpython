# expect:
# <b><i>hi</i></b>
def bold(f):
    def w(*a):
        return '<b>' + f(*a) + '</b>'
    return w
def italic(f):
    def w(*a):
        return '<i>' + f(*a) + '</i>'
    return w
@bold
@italic
def text(s):
    return s
print(text('hi'))
# asmpython (beta/3.14.0) rejects at compile: [E023] *expr argument unpacking requires a tuple with known element types
