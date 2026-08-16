# probes: stat.S_ISDIR reads a mode word
# expect:
# True
# False
import stat

print(stat.S_ISDIR(0o040755))
print(stat.S_ISREG(0o040755))
