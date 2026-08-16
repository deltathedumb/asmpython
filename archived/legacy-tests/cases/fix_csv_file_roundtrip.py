# probes: csv writes and re-reads a file
# expect:
# [['name', 'count'], ['ada', '2']]
import csv
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_csv.csv")
try:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "count"])
        writer.writerow(["ada", 2])
    with open(path, "r", encoding="utf-8", newline="") as handle:
        print([row for row in csv.reader(handle)])
finally:
    if os.path.exists(path):
        os.remove(path)
