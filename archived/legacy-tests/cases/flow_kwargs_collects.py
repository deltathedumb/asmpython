# probes: **kwargs collects the extra keywords
# expect:
# a=1,b=2
def describe(**fields):
    return ",".join(k + "=" + str(v) for k, v in fields.items())


print(describe(a=1, b=2))
