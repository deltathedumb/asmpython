# probes: sqlite3 round-trips a row in memory
# expect:
# ('ada', 36)
import sqlite3

connection = sqlite3.connect(":memory:")
connection.execute("CREATE TABLE people (name TEXT, age INTEGER)")
connection.execute("INSERT INTO people VALUES (?, ?)", ("ada", 36))
for row in connection.execute("SELECT name, age FROM people"):
    print(row)
connection.close()
