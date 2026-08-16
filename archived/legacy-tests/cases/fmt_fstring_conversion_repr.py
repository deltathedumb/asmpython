# probes: !r formats with repr
# expect:
# 'text'
# text
s = "text"
print(f"{s!r}")
print(f"{s}")
