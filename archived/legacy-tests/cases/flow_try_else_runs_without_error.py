# probes: try/else runs the else only on success
# expect:
# clean
# caught
def attempt(fail):
    try:
        if fail:
            raise ValueError("x")
    except ValueError:
        return "caught"
    else:
        return "clean"


print(attempt(False))
print(attempt(True))
