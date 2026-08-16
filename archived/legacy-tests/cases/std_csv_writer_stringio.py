# probes: csv.writer writes rows to a text stream
# expect:
# a,b
# 1,2
import csv
import io

buf = io.StringIO()
writer = csv.writer(buf, lineterminator="\n")
writer.writerow(["a", "b"])
writer.writerow([1, 2])
print(buf.getvalue(), end="")
