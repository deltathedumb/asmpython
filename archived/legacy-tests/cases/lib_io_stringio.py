# expect:
# hello world
import io
buf = io.StringIO()
buf.write('hello')
buf.write(' world')
print(buf.getvalue())
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
