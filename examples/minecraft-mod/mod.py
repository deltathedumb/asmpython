"""A Minecraft mod written in Python, compiled to JVM bytecode by asmpython.

No loader mod and no interpreter: this file becomes the mod jar. It drives real
NeoForge APIs through `java`, and registers a real item -- which NeoForge takes
as a Supplier, so the Python function that builds the item is handed over as
one through `jproxy`.
"""
import java
import java.net.neoforged.neoforge.registries as registries

from asmpython import Public, access


@access(Public)
def make_bat():
    """Supplier<Item>: builds the Item NeoForge asks for during registration."""
    # Item.Properties is a nested class, so it is named by its binary name --
    # `Item$Properties` is not something Python can spell as an attribute.
    Properties = java.jclass("net.minecraft.world.item.Item$Properties")
    props = java.jnew(Properties)
    java.jcall_i(props, "stacksTo", 1)
    java.jcall_i(props, "durability", 250)

    Item = java.jclass("net.minecraft.world.item.Item")
    return java.jnew_o(Item, props)


@access(Public)
def on_construct(mod_bus):
    """NeoForge hands a mod its event bus; everything hangs off that."""
    DeferredRegister = java.jclass(
        "net.neoforged.neoforge.registries.DeferredRegister")
    items = java.jcall_s(DeferredRegister, "createItems", "demomod")

    supplier = java.jproxy("java.util.function.Supplier", "make_bat")
    java.jcall_so(items, "register", "bat", supplier)
    java.jcall_o(items, "register", mod_bus)

    print("[demomod] registered demomod:bat from Python")
    return 0


def main():
    print("[demomod] python mod body running")
