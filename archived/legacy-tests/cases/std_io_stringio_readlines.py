# probes: StringIO replays written lines
# expect:
# ['a\n', 'b\n']
import io

buf = io.StringIO("a\nb\n")
print(buf.readlines())
