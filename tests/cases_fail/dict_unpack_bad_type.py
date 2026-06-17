# expect-error: dict unpacking requires a dict (got int)
x = 5
d = {**x}
print(d)
