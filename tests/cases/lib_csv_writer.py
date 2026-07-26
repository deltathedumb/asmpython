# expect:
# a,b
import csv, io
out = io.StringIO()
w = csv.writer(out)
w.writerow(['a', 'b'])
print(out.getvalue().strip())
# asmpython (beta/3.14.0) rejects at compile: [P002] expected NEWLINE, got OP ','
