from asmpython.assembly import Assembly

asm = Assembly()
asm.section(".text")
asm.global_("main")
asm.label("main")
asm.push("rbp")
asm.mov("rbp", "rsp")
asm.sub("rsp", "32")
asm.mov("rax", "7")
asm.add("rax", "3")
print(asm.body)
