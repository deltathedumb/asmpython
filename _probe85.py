# probe85: context managers (with statement), __enter__/__exit__
class Timer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.active = False

    def __enter__(self) -> "Timer":
        self.active = True
        print("start " + self.name)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.active = False
        print("end " + self.name)
        return False

with Timer("test") as t:
    print("inside")
    print(t.active)   # True

print(t.active)   # False

# Context manager with exception
class Suppress:
    def __init__(self, exc_type) -> None:
        self._exc = exc_type

    def __enter__(self) -> "Suppress":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return True   # suppress all

with Suppress(ValueError):
    raise ValueError("suppressed")

print("after suppress")   # after suppress
