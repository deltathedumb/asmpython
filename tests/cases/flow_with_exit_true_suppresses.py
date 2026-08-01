# probes: __exit__ returning True swallows the error
# expect:
# continued
class Swallow:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return True


with Swallow():
    raise ValueError("boom")
print("continued")
