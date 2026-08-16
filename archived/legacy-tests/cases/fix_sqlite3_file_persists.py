# probes: a sqlite3 file survives reconnecting
# expect:
# 7
import os
import sqlite3
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_sqlite.db")
try:
    first = sqlite3.connect(path)
    first.execute("CREATE TABLE t (v INTEGER)")
    first.execute("INSERT INTO t VALUES (7)")
    first.commit()
    first.close()

    second = sqlite3.connect(path)
    print(second.execute("SELECT v FROM t").fetchone()[0])
    second.close()
finally:
    if os.path.exists(path):
        os.remove(path)
