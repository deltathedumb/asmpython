# expect:
# using
# closed
from contextlib import closing
class R:
    def close(self):
        print('closed')
with closing(R()) as r:
    print('using')
# asmpython (beta/3.14.0) rejects at compile: unsupported expr MethodCall (str.close)
