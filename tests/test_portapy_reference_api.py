from __future__ import annotations

import unittest

from portapy import Runtime, Status, ValueKind


class PortaPyReferenceApiTests(unittest.TestCase):
    def test_exec_get_global_and_checked_conversion(self) -> None:
        runtime = Runtime()
        self.assertEqual(runtime.exec_utf8("answer = 40 + 2\n"), Status.OK)
        status, handle = runtime.get_global("answer")
        self.assertEqual(status, Status.OK)
        self.assertNotEqual(handle, 0)
        self.assertEqual(runtime.value_kind(handle), (Status.OK, ValueKind.INT))
        self.assertEqual(runtime.as_int(handle), (Status.OK, 42))

    def test_eval_returns_owned_handle(self) -> None:
        runtime = Runtime()
        status, handle = runtime.eval_utf8("6 * 7")
        self.assertEqual(status, Status.OK)
        self.assertEqual(runtime.as_int(handle), (Status.OK, 42))
        self.assertEqual(runtime.retain(handle), Status.OK)
        self.assertEqual(runtime.release(handle), Status.OK)
        self.assertEqual(runtime.as_int(handle), (Status.OK, 42))
        self.assertEqual(runtime.release(handle), Status.OK)
        self.assertEqual(runtime.as_int(handle)[0], Status.INVALID_HANDLE)

    def test_function_call_uses_vm_call_path(self) -> None:
        runtime = Runtime()
        self.assertEqual(
            runtime.exec_utf8("def add(a, b):\n    return a + b\n"),
            Status.OK,
        )
        status, function = runtime.get_global("add")
        self.assertEqual(status, Status.OK)
        status, a = runtime.box_int(20)
        self.assertEqual(status, Status.OK)
        status, b = runtime.box_int(22)
        self.assertEqual(status, Status.OK)
        status, result = runtime.call(function, [a, b])
        self.assertEqual(status, Status.OK)
        self.assertEqual(runtime.as_int(result), (Status.OK, 42))

    def test_utf8_and_bytes_are_distinct_checked_types(self) -> None:
        runtime = Runtime()
        status, text = runtime.box_utf8("héllo")
        self.assertEqual(status, Status.OK)
        self.assertEqual(runtime.as_utf8(text), (Status.OK, "héllo".encode("utf-8")))
        self.assertEqual(runtime.as_bytes(text)[0], Status.TYPE_ERROR)

        status, data = runtime.box_bytes(b"\x00\xff")
        self.assertEqual(status, Status.OK)
        self.assertEqual(runtime.as_bytes(data), (Status.OK, b"\x00\xff"))
        self.assertEqual(runtime.as_utf8(data)[0], Status.TYPE_ERROR)

    def test_compile_and_runtime_errors_are_structured(self) -> None:
        runtime = Runtime()
        self.assertEqual(runtime.exec_utf8("def broken(:\n"), Status.COMPILE_ERROR)
        compile_error = runtime.last_error()
        self.assertIsNotNone(compile_error)
        self.assertEqual(compile_error.status, Status.COMPILE_ERROR)

        self.assertEqual(runtime.exec_utf8("1 / 0\n"), Status.RUNTIME_ERROR)
        runtime_error = runtime.last_error()
        self.assertIsNotNone(runtime_error)
        self.assertEqual(runtime_error.type_name, "ZeroDivisionError")
        self.assertIn("division", runtime_error.message)

    def test_destroyed_runtime_rejects_future_work(self) -> None:
        runtime = Runtime()
        self.assertEqual(runtime.close(), Status.OK)
        self.assertEqual(runtime.exec_utf8("x = 1\n"), Status.CLOSED)
        self.assertEqual(runtime.eval_utf8("1")[0], Status.CLOSED)
        self.assertEqual(runtime.close(), Status.CLOSED)


if __name__ == "__main__":
    unittest.main()
