# probe71: exception raising and custom exceptions
class AppError(Exception):
    def __init__(self, msg: str) -> None:
        self.msg = msg

    def __str__(self) -> str:
        return "AppError: " + self.msg

class ValidationError(AppError):
    def __init__(self, field: str, msg: str) -> None:
        self.field = field
        self.msg = msg

    def __str__(self) -> str:
        return "ValidationError[" + self.field + "]: " + self.msg

def validate_age(age: int) -> None:
    if age < 0:
        raise ValidationError("age", "must be non-negative")
    if age > 150:
        raise ValidationError("age", "unrealistically large")

try:
    validate_age(-5)
except ValidationError as e:
    print(str(e))   # ValidationError[age]: must be non-negative

try:
    validate_age(200)
except AppError as e:
    print(e.msg)    # unrealistically large

try:
    validate_age(25)
    print("ok")    # ok
except ValidationError as e:
    print("fail")

# re-raise
def risky() -> int:
    try:
        raise ValueError("oops")
    except ValueError:
        raise

try:
    risky()
except ValueError as e:
    print(str(e))   # oops
