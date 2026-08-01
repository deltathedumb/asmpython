# probes: a leaked StopIteration becomes RuntimeError
# expect:
# converted to RuntimeError
def gen():
    raise StopIteration("leaked")
    yield 1


try:
    list(gen())
    print("no error")
except RuntimeError:
    print("converted to RuntimeError")
