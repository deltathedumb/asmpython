# guards: live_definition_compat_fixes
# expect:
# ran
class Used:
    def run(self):
        return "ran"


class NeverConstructed:
    def broken(self):
        return self.does_not_exist.at_all()


print(Used().run())
