# probes: zipfile writes and reads a member in memory
# expect:
# ['hello.txt']
# contents
import io
import zipfile

buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as archive:
    archive.writestr("hello.txt", "contents")
with zipfile.ZipFile(buf) as archive:
    print(archive.namelist())
    print(archive.read("hello.txt").decode("utf-8"))
