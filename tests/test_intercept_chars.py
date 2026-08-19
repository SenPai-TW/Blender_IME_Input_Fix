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
