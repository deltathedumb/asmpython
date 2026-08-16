# probes: adjacent f-strings concatenate
# expect:
# a=1 b=2
a = 1
b = 2
text = (
    f"a={a} "
    f"b={b}"
)
print(text)
