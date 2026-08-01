# probes: __exit__ runs on the break path
# expect:
# body 1
# exit ran
# exit ran
# done
class Trace:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        print("exit ran")
        return False


for i in [1, 2, 3]:
    with Trace():
        if i == 2:
            break
        print("body", i)
print("done")
