# probes: codecs.encode applies a named codec
# expect:
# nop
import codecs

print(codecs.encode("abc", "rot13"))
