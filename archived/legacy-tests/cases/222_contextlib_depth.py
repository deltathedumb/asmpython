# expect:
# entered
# exited
# hello

from contextlib import closing, ExitStack, AbstractContextManager

class Opener(AbstractContextManager):
    def __init__(self) -> None:
        self.closed: int = 0

    def __enter__(self) -> Opener:
        print("entered")
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        print("exited")
        self.closed = 1
        return 0

with Opener() as o:
    pass

stack = ExitStack()
stack.__enter__()
stack.__exit__(0, 0, 0)

print("hello")
