# tier: spec
# ref: library/exceptions.html#BaseException
# expect:
# BaseException Fatal
# True
# False
# False
class Fatal(BaseException):
    pass

try:
    try:
        raise Fatal("stop")
    except Exception:
        print("wrongly-caught")
except BaseException as e:
    print("BaseException", type(e).__name__)

print(issubclass(KeyboardInterrupt, BaseException))
print(issubclass(KeyboardInterrupt, Exception))
print(issubclass(SystemExit, Exception))
