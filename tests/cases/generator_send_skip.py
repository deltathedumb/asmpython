# expect:
# got hello
def echo():
    while True:
        x = yield
        print('got', x)
g = echo()
next(g)
g.send('hello')
# asmpython (beta/3.14.0) rejects at compile: [P001] unexpected token KEYWORD 'yield'
