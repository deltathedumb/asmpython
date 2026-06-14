# expect-error: 'NotAnException' is not an exception type
try:
    raise ValueError("v")
except NotAnException:
    pass
