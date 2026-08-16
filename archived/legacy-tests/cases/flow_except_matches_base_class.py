# probes: except catches a subclass of the named type
# expect:
# DiskError
# disk
class AppError(Exception):
    pass


class DiskError(AppError):
    pass


try:
    raise DiskError("disk")
except AppError as err:
    print(type(err).__name__)
    print(str(err))
