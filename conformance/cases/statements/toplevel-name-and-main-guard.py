# tier: spec
# ref: reference/toplevel_components.html
# expect:
# True
# running-as-script
# str
print(__name__ == "__main__")
if __name__ == "__main__":
    print("running-as-script")
else:
    print("imported")
print(type(__name__).__name__)
