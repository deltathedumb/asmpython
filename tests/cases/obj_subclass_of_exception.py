# probes: an Exception subclass is catchable as Exception
# expect:
# AppError
# failed
class AppError(Exception):
    pass


try:
    raise AppError("failed")
except Exception as err:
    print(type(err).__name__)
    print(str(err))
