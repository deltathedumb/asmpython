# expect:
# [['a', 'b', 'c'], ['1', '2', '3']]
import csv, io
r = csv.reader(io.StringIO('a,b,c\n1,2,3'))
print([row for row in r])
# asmpython (beta/3.14.0) rejects at compile: [P002] expected NEWLINE, got OP ','
