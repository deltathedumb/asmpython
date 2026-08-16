# probes: an atexit handler runs at interpreter exit
# expect:
# main done
# atexit ran
import atexit


def farewell():
    print("atexit ran")


atexit.register(farewell)
print("main done")
