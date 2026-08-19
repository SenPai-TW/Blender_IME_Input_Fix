import ast
import unittest
from pathlib import Path


POC_PATH = Path(__file__).resolve().parents[1] / "ime_fix_poc.py"


def load_intercept_chars():
    """Read the policy constant without importing Blender-only modules."""
    tree = ast.parse(POC_PATH.read_text(encoding="utf-8"), filename=str(POC_PATH))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "INTERCEPT_CHARS"
            for target in node.targets
        ):
            continue
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "set"
            and len(node.value.args) == 1
            and isinstance(node.value.args[0], ast.Constant)
            and isinstance(node.value.args[0].value, str)
        ):
            return set(node.value.args[0].value)
        raise AssertionError("INTERCEPT_CHARS must remain a set made from one string")
    raise AssertionError("INTERCEPT_CHARS was not found")


def load_bopomofo_raw_policy():
    """Load the pure Raw Input policy without importing Blender or Win32 APIs."""
    tree = ast.parse(POC_PATH.read_text(encoding="utf-8"), filename=str(POC_PATH))
    assignment_names = {"LANGID_ZH_TW", "BOPOMOFO_TOP_ROW_VKEYS"}
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in assignment_names
            for target in node.targets
        ):
            selected.append(node)
        elif (
            isinstance(node, ast.FunctionDef)
            and node.name == "_should_suppress_bopomofo_raw"
        ):
            selected.append(node)

    if not any(
        isinstance(node, ast.FunctionDef)
        and node.name == "_should_suppress_bopomofo_raw"
        for node in selected
    ):
        raise AssertionError("_should_suppress_bopomofo_raw was not found")

    namespace = {"__builtins__": {"bool": bool}}
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    exec(
        compile(module, str(POC_PATH), "exec"),
        namespace,
        namespace,
    )
    return namespace["_should_suppress_bopomofo_raw"]


def load_handle_message(namespace):
    """Load the message router with injected Win32 stand-ins."""
    tree = ast.parse(POC_PATH.read_text(encoding="utf-8"), filename=str(POC_PATH))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_handle_message"
    ]
    if not selected:
        raise AssertionError("_handle_message was not found")
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    exec(compile(module, str(POC_PATH), "exec"), namespace, namespace)
    return namespace["_handle_message"]


class InterceptCharsTests(unittest.TestCase):
    def test_equals_uses_the_existing_intercept_path(self):
        self.assertIn("=", load_intercept_chars())

    def test_minus_uses_the_existing_intercept_path(self):
        self.assertIn("-", load_intercept_chars())

    def test_existing_known_good_plus_remains_intercepted(self):
        self.assertIn("+", load_intercept_chars())

    def test_recently_recovered_punctuation_remains_pass_through(self):
        for character in "[]':\"<>?":
            with self.subTest(character=character):
                self.assertNotIn(character, load_intercept_chars())

    def test_intercepted_result_passes_character_to_lparam_builder(self):
        built_for = []
        forwarded = []

        class State:
            fix_enabled = True
            diag_only = False
            intercept_count = 0
            pass_count = 0

        def make_char_lparam(character):
            if not isinstance(character, str) or len(character) != 1:
                raise TypeError("VkKeyScanW requires one Unicode character")
            built_for.append(character)
            return 0x00100001

        namespace = {
            "WM_INPUT": 0x00FF,
            "WM_CHAR": 0x0102,
            "WM_KEYDOWN": 0x0100,
            "WM_IME_COMPOSITION": 0x010F,
            "WM_IME_CHAR": 0x0286,
            "WM_IME_NOTIFY": 0x0282,
            "GCS_RESULTSTR": 0x0800,
            "MSG_NAMES": {},
            "INTERCEPT_CHARS": {"="},
            "_get_result_str": lambda _hwnd: "=",
            "_make_char_lparam": make_char_lparam,
            "_add_log": lambda _state, _text: None,
        }
        handle_message = load_handle_message(namespace)

        result = handle_message(
            State(),
            lambda *args: forwarded.append(args) or 0,
            101,
            0x010F,
            0,
            0x0800,
        )

        self.assertEqual(result, 0)
        self.assertEqual(built_for, ["="])
        self.assertEqual(forwarded, [(101, 0x0102, ord("="), 0x00100001)])


class BopomofoRawInputPolicyTests(unittest.TestCase):
    def test_zh_tw_native_top_row_bopomofo_keys_are_suppressed(self):
        should_suppress = load_bopomofo_raw_policy()
        top_row_vkeys = (*range(0x30, 0x3A), 0xBD)
        for vkey in top_row_vkeys:
            with self.subTest(vkey=vkey):
                self.assertTrue(should_suppress(vkey, True, True, 0x0404))

    def test_numpad_digits_and_minus_are_not_suppressed(self):
        should_suppress = load_bopomofo_raw_policy()
        numpad_vkeys = (*range(0x60, 0x6A), 0x6D)
        for vkey in numpad_vkeys:
            with self.subTest(vkey=vkey):
                self.assertFalse(should_suppress(vkey, True, True, 0x0404))

    def test_top_row_passes_in_english_mode(self):
        should_suppress = load_bopomofo_raw_policy()
        self.assertFalse(should_suppress(0x31, True, False, 0x0404))

    def test_top_row_passes_for_other_chinese_input_locale(self):
        should_suppress = load_bopomofo_raw_policy()
        self.assertFalse(should_suppress(0x31, True, True, 0x0804))

    def test_key_up_is_not_suppressed(self):
        should_suppress = load_bopomofo_raw_policy()
        self.assertFalse(should_suppress(0x31, False, True, 0x0404))


if __name__ == "__main__":
    unittest.main()
