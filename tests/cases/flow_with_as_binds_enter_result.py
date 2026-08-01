# probes: `as` binds what __enter__ returned
# expect:
# handle
class Resource:
    def __enter__(self):
        return "handle"

    def __exit__(self, exc_type, exc, tb):
        return False


with Resource() as handle:
    print(handle)
