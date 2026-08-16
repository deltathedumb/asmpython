# probes: a bare raise re-raises the active exception
# expect:
# handling
# outer saw original
try:
    try:
        raise ValueError("original")
    except ValueError:
        print("handling")
        raise
except ValueError as err:
    print("outer saw " + str(err))
