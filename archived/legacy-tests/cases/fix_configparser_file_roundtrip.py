# probes: configparser writes and re-reads an INI file
# expect:
# ada
import configparser
import os
import tempfile

path = os.path.join(tempfile.gettempdir(), "asmpy_fix_config.ini")
try:
    writing = configparser.ConfigParser()
    writing["main"] = {"name": "ada"}
    with open(path, "w", encoding="utf-8") as handle:
        writing.write(handle)
    reading = configparser.ConfigParser()
    reading.read(path, encoding="utf-8")
    print(reading["main"]["name"])
finally:
    if os.path.exists(path):
        os.remove(path)
