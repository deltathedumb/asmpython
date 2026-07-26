# expect:
# <div>
# content
# </div>
from contextlib import contextmanager
@contextmanager
def tag(name):
    print('<' + name + '>')
    yield
    print('</' + name + '>')
with tag('div'):
    print('content')
# asmpython (beta/3.14.0) rejects at compile: unsupported stmt YieldStmt
