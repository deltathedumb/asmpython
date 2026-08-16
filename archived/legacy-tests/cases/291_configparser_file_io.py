# expect:
# myvalue
# read done

from configparser import ConfigParser

cp = ConfigParser()
cp.read_string("[section]\nkey = myvalue\n")
print(cp.get("section", "key"))
print("read done")
