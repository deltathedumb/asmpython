# probes: !s formats with str
# expect:
# STR
# REPR
# STR
class Both:
    def __str__(self):
        return "STR"

    def __repr__(self):
        return "REPR"


b = Both()
print(f"{b!s}")
print(f"{b!r}")
print(f"{b}")
