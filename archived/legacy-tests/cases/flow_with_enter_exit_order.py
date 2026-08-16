# probes: __enter__ and __exit__ bracket the body
# expect:
# enter
# body
# exit
class Trace:
    def __enter__(self):
        print("enter")
        return self

    def __exit__(self, exc_type, exc, tb):
        print("exit")
        return False


with Trace():
    print("body")
