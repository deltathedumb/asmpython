# expect:
# entered
# done

from contextlib import ExitStack

stack = ExitStack()
stack.__enter__()
print("entered")
stack.__exit__(0, 0, 0)
print("done")
