# tier: spec
# ref: peps.python.org/pep-0342/
# expect:
# got a
# got b
def echo():
    while True:
        got = yield
        if got is None:
            return
        print('got', got)

g = echo()
next(g)
g.send('a')
g.send('b')
