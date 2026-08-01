# probes: @contextmanager turns a generator into a manager
# expect:
# open job
# JOB
# close job
import contextlib


@contextlib.contextmanager
def section(name):
    print("open " + name)
    yield name.upper()
    print("close " + name)


with section("job") as label:
    print(label)
