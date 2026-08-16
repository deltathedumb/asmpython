# expect:
# alice
import csv, io
r = csv.DictReader(io.StringIO('name,age\nalice,30'))
rows = list(r)
print(rows[0]['name'])
# asmpython (beta/3.14.0) rejects at compile: [P002] expected NEWLINE, got OP ','
