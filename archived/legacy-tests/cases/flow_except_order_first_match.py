# probes: the first matching except clause wins
# expect:
# value
try:
    raise ValueError("v")
except TypeError:
    print("type")
except ValueError:
    print("value")
except Exception:
    print("generic")
