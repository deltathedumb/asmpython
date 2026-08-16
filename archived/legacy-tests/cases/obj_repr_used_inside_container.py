# probes: a container renders elements with repr
# expect:
# [<repr>]
class Tagged:
    def __repr__(self):
        return "<repr>"

    def __str__(self):
        return "<str>"


print([Tagged()])
