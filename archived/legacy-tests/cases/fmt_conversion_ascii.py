# probes: !a formats with ascii()
# expect:
# 'caf\xe9'
s = "café"
print(f"{s!a}")
