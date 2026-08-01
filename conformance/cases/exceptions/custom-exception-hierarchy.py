# tier: spec
# ref: library/exceptions.html#exception-hierarchy
# expect:
# SubError boom
# True
# AppError
class AppError(Exception):
    pass

class SubError(AppError):
    pass

try:
    raise SubError("boom")
except AppError as e:
    print(type(e).__name__, e)
    print(isinstance(e, Exception))

print(SubError.__mro__[1].__name__)
