import ast
import unittest
from pathlib import Path


POC_PATH = Path(__file__).resolve().parents[1] / "blender_ime_fix" / "core.py"


def load_hook_manager():
    """Load the pure lifecycle module without importing Blender or Win32."""
    tree = ast.parse(POC_PATH.read_text(encoding="utf-8"), filename=str(POC_PATH))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_HookManager"
    ]
    if not selected:
        raise AssertionError("_HookManager was not found")

    namespace = {}
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    exec(compile(module, str(POC_PATH), "exec"), namespace, namespace)
    return namespace["_HookManager"]


def load_pure_function(name):
    tree = ast.parse(POC_PATH.read_text(encoding="utf-8"), filename=str(POC_PATH))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    if not selected:
        raise AssertionError(f"{name} was not found")
    namespace = {}
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    exec(compile(module, str(POC_PATH), "exec"), namespace, namespace)
    return namespace[name]


class FakeHookAdapter:
    def __init__(self, targets=()):
        self.targets = list(targets)
        self.valid_windows = set(targets)
        self.install_calls = []
        self.remove_calls = []
        self.install_failures = set()
        self.remove_failures = set()

    def target_windows(self):
        return list(self.targets)

    def is_window(self, hwnd):
        return hwnd in self.valid_windows

    def install(self, hwnd, callback, subclass_id):
        self.install_calls.append((hwnd, callback, subclass_id))
        return hwnd not in self.install_failures

    def remove(self, hwnd, callback, subclass_id):
        self.remove_calls.append((hwnd, callback, subclass_id))
        return hwnd not in self.remove_failures


class HookLifecycleTests(unittest.TestCase):
    def make_manager(self, targets=(101,)):
        manager_type = load_hook_manager()
        adapter = FakeHookAdapter(targets)
        callback = object()
        manager = manager_type(adapter, callback, 0x494D4532)
        return manager, adapter, callback

    def test_start_is_idempotent_and_never_stacks_the_same_hook(self):
        manager, adapter, callback = self.make_manager()

        self.assertTrue(manager.start())
        self.assertTrue(manager.start())

        self.assertEqual(
            adapter.install_calls,
            [(101, callback, 0x494D4532)],
        )
        self.assertEqual(manager.phase, "RUNNING")
        self.assertEqual(manager.hooked_windows, (101,))

    def test_stop_removes_the_exact_callback_and_subclass_id(self):
        manager, adapter, callback = self.make_manager()
        manager.start()

        self.assertTrue(manager.stop())

        self.assertEqual(
            adapter.remove_calls,
            [(101, callback, 0x494D4532)],
        )
        self.assertEqual(manager.phase, "STOPPED")
        self.assertEqual(manager.hooked_windows, ())
        self.assertTrue(manager.can_release)

    def test_failed_remove_retains_ownership_and_blocks_reinstall(self):
        manager, adapter, callback = self.make_manager()
        manager.start()
        adapter.remove_failures.add(101)

        self.assertFalse(manager.stop())

        self.assertEqual(manager.phase, "DEGRADED")
        self.assertEqual(manager.hooked_windows, (101,))
        self.assertFalse(manager.can_release)
        self.assertFalse(manager.start())
        self.assertEqual(adapter.install_calls, [(101, callback, 0x494D4532)])

    def test_reconcile_adds_new_windows_without_rehooking_existing_ones(self):
        manager, adapter, callback = self.make_manager()
        manager.start()
        adapter.targets.append(202)
        adapter.valid_windows.add(202)

        self.assertTrue(manager.reconcile())

        self.assertEqual(
            adapter.install_calls,
            [
                (101, callback, 0x494D4532),
                (202, callback, 0x494D4532),
            ],
        )
        self.assertEqual(manager.hooked_windows, (101, 202))

    def test_destroyed_window_can_be_released_without_remove_call(self):
        manager, adapter, _callback = self.make_manager()
        manager.start()
        adapter.valid_windows.remove(101)
        adapter.targets.clear()

        self.assertTrue(manager.reconcile())

        self.assertEqual(adapter.remove_calls, [])
        self.assertEqual(manager.hooked_windows, ())
        self.assertTrue(manager.can_release)

    def test_old_direct_wndproc_replacement_is_not_used(self):
        source = POC_PATH.read_text(encoding="utf-8")
        self.assertNotIn("SetWindowLongPtrW", source)
        self.assertIn("SetWindowSubclass", source)
        self.assertIn("RemoveWindowSubclass", source)
        self.assertIn("DefSubclassProc", source)

    def test_only_public_windows_dlls_and_no_blender_memory_offsets_are_used(self):
        source = POC_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(POC_PATH))
        loaded_dlls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "ctypes"
                and node.func.attr == "WinDLL"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                continue
            loaded_dlls.append(node.args[0].value.lower())

        self.assertEqual(
            set(loaded_dlls),
            {"user32", "imm32", "comctl32", "kernel32"},
        )
        self.assertNotIn("as_pointer(", source)
        self.assertNotIn("offset_wm", source)
        self.assertNotIn("offset_WorkSpace", source)

    def test_runtime_reload_stops_the_previous_owner_before_replacement(self):
        replace_runtime = load_pure_function("_replace_runtime")

        class Runtime:
            def __init__(self, stop_result=True):
                self.stop_result = stop_result
                self.shutdown_calls = 0

            def shutdown(self):
                self.shutdown_calls += 1
                return self.stop_result

        class Store:
            instance = None

        store = Store()
        previous = Runtime()
        replacement = Runtime()
        store.instance = previous

        self.assertTrue(replace_runtime(store, replacement))
        self.assertEqual(previous.shutdown_calls, 1)
        self.assertIs(store.instance, replacement)

    def test_runtime_reload_keeps_old_owner_when_shutdown_fails(self):
        replace_runtime = load_pure_function("_replace_runtime")

        class Runtime:
            def __init__(self, stop_result):
                self.stop_result = stop_result

            def shutdown(self):
                return self.stop_result

        class Store:
            instance = None

        store = Store()
        previous = Runtime(False)
        replacement = Runtime(True)
        store.instance = previous

        self.assertFalse(replace_runtime(store, replacement))
        self.assertIs(store.instance, previous)


if __name__ == "__main__":
    unittest.main()
