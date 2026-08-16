# probes: a nested function reads the enclosing local
# expect:
# captured
def outer():
    message = "captured"

    def inner():
        return message

    return inner()


print(outer())
