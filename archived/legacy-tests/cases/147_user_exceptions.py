# expect:
# caught: something went wrong
# caught ParseError: bad input at line 5
# caught LookupError subclass
# finally ran
# NetworkError: connection refused

class AppError(Exception):
    pass

class ParseError(AppError):
    pass

class NetworkError(Exception):
    pass

# Basic user exception: raise and catch
try:
    raise AppError("something went wrong")
except AppError as e:
    print("caught:", e)

# Subclass raise, catch by parent
try:
    raise ParseError("bad input at line 5")
except AppError as e:
    print("caught ParseError:", e)

# Exception hierarchy: user class deriving from builtin LookupError
class MyLookupError(LookupError):
    pass

try:
    raise MyLookupError("key missing")
except LookupError:
    print("caught LookupError subclass")

# finally still runs when user exception propagates
try:
    try:
        raise AppError("inner")
    finally:
        print("finally ran")
except AppError as e:
    pass

# No-arg raise: uses class name as message
try:
    raise NetworkError("connection refused")
except NetworkError as e:
    print("NetworkError:", e)
