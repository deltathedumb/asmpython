# probes: Path.iterdir lists a directory
# expect:
# ['one.txt', 'two.txt']
import pathlib
import shutil
import tempfile

root = pathlib.Path(tempfile.gettempdir()) / "asmpy_fix_iterdir"
try:
    root.mkdir(exist_ok=True)
    (root / "one.txt").write_text("1", encoding="utf-8")
    (root / "two.txt").write_text("2", encoding="utf-8")
    print(sorted(p.name for p in root.iterdir()))
finally:
    shutil.rmtree(root, ignore_errors=True)
