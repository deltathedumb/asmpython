# expect:
# text/html
import mimetypes
print(mimetypes.guess_type('file.html')[0])
# asmpython (beta/3.14.0) rejects at compile: asmpython: pyinbin fallback failed: ImportError: no pyinbin module named 'mimetypes'
