# Helper module used by 100_user_import.py and 101_user_import_qualified.py.
# Compiled standalone it produces no output, so this passes as a trivial test.
# expect:

def add(a: int, b: int) -> int:
    return a + b

def multiply(a: int, b: int) -> int:
    return a * b

def greet(name: str) -> str:
    return "hello " + name

ANSWER = 42
