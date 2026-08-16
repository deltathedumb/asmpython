# expect:
# a/b/c.py/sub
# c.py
# a/b
# .py
# c
# 1
# hello

from pathlib import Path

p = Path("a/b/c.py")
q = p.joinpath("sub")
print(q.p)
print(p.name)
print(p.parent.p)
print(p.suffix)
print(p.stem)

dot = Path(".")
print(dot.exists())

tmp = Path("_test_pathlib_247.txt")
tmp.write_text("hello")
print(tmp.read_text())
tmp.unlink()
