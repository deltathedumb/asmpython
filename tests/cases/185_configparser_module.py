# expect:
# value1
# 42
# section1

from configparser import ConfigParser

cfg = ConfigParser()
cfg.add_section("section1")
cfg.set("section1", "key1", "value1")
cfg.set("section1", "num", "42")

print(cfg.get("section1", "key1"))
print(cfg.getint("section1", "num"))

secs = cfg.sections()
print(secs[0])
