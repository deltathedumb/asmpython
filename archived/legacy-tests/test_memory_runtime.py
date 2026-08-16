from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from asmpython.runtime import (
    DoubleReleaseError,
    MemoryManager,
    Ownership,
)


@dataclass
class Node:
    name: str
    children: list["Node"] = field(default_factory=list)


def trace_node(value: object):
    assert isinstance(value, Node)
    return value.children


def test_owned_handles_release_and_weak_handles_expire() -> None:
    manager = MemoryManager("test")
    node = Node("root")
    handle = manager.track(node, trace=trace_node)
    weak = handle.weak()
    assert weak.value is node
    handle.release()
    assert manager.collect_cycles() == 1
    assert weak.value is None
    manager.teardown()


def test_cycle_collection_collects_unrooted_cycle() -> None:
    manager = MemoryManager("cycle")
    left = Node("left")
    right = Node("right")
    left.children.append(right)
    right.children.append(left)
    left_id = manager.track_internal(left, trace=trace_node)
    right_id = manager.track_internal(right, trace=trace_node)
    manager.refresh_edges()
    assert manager.contains(left_id)
    assert manager.contains(right_id)
    assert manager.collect_cycles() == 2
    assert not manager.contains(left_id)
    assert not manager.contains(right_id)


def test_root_keeps_reachable_cycle_alive() -> None:
    manager = MemoryManager("reachable")
    left = Node("left")
    right = Node("right")
    left.children.append(right)
    right.children.append(left)
    root = manager.track(left, trace=trace_node)
    manager.track_internal(right, trace=trace_node)
    assert manager.collect_cycles() == 0
    root.release()
    assert manager.collect_cycles() == 2


def test_finalizers_run_child_before_parent() -> None:
    manager = MemoryManager("finalizers")
    events: list[str] = []
    parent = Node("parent")
    child = Node("child")
    parent.children.append(child)
    manager.track_internal(parent, trace=trace_node, finalizer=lambda value: events.append(value.name))
    manager.track_internal(child, trace=trace_node, finalizer=lambda value: events.append(value.name))
    manager.collect_cycles()
    assert events == ["child", "parent"]


def test_transfer_and_double_release_rules() -> None:
    manager = MemoryManager("transfer")
    handle = manager.track(Node("value"), ownership=Ownership.OWNED)
    transferred = handle.transfer()
    with pytest.raises(DoubleReleaseError):
        handle.release()
    transferred.release()
    with pytest.raises(DoubleReleaseError):
        transferred.release()
    assert manager.collect_cycles() == 1


def test_pin_is_a_collection_root() -> None:
    manager = MemoryManager("pin")
    node = Node("pinned")
    handle = manager.track(node, ownership=Ownership.PINNED, trace=trace_node)
    handle.release()
    assert manager.collect_cycles() == 0
    manager.unpin(handle.identifier)
    assert manager.collect_cycles() == 1
