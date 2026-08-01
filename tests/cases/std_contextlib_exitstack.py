# probes: ExitStack unwinds in reverse order
# expect:
# body
# closed b
# closed a
import contextlib


@contextlib.contextmanager
def named(name):
    yield name
    print("closed " + name)


with contextlib.ExitStack() as stack:
    stack.enter_context(named("a"))
    stack.enter_context(named("b"))
    print("body")
