# expect:
# caught as base SubErr
class BaseErr(Exception):
    pass
class SubErr(BaseErr):
    pass
try:
    raise SubErr('x')
except BaseErr as e:
    print('caught as base', type(e).__name__)
# asmpython (beta/3.14.0) MISMATCH: prints 'caught as base str\n' (wrong).
