import ast
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ADDON_INIT = ROOT / "blender_ime_fix" / "__init__.py"
CORE_PATH = ROOT / "blender_ime_fix" / "core.py"
LICENSE_PATH = ROOT / "LICENSE"
BUILD_SCRIPT = ROOT / "tools" / "build_addon.py"


def load_bl_info():
    tree = ast.parse(ADDON_INIT.read_text(encoding="utf-8"), filename=str(ADDON_INIT))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "bl_info"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("bl_info was not found")


def load_build_module():
    spec = importlib.util.spec_from_file_location("build_addon", BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AddonPackageTests(unittest.TestCase):
    def test_legacy_addon_metadata_declares_supported_floor(self):
        info = load_bl_info()
        self.assertEqual(info["name"], "Blender IME Input Fix")
        self.assertEqual(info["version"], (0, 1, 0))
        self.assertEqual(info["blender"], (2, 80, 0))
        self.assertEqual(info["category"], "System")

    def test_package_facade_only_delegates_lifecycle(self):
        tree = ast.parse(ADDON_INIT.read_text(encoding="utf-8"), filename=str(ADDON_INIT))
        function_names = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertEqual(function_names, {"register", "unregister"})
        self.assertNotIn("ctypes", ADDON_INIT.read_text(encoding="utf-8"))

    def test_runtime_store_stays_in_package_namespace(self):
        source = CORE_PATH.read_text(encoding="utf-8")
        self.assertIn('ADDON_PACKAGE = __package__ or "blender_ime_fix"', source)
        self.assertIn('RUNTIME_STORE_MODULE = ADDON_PACKAGE + "._runtime_store"', source)
        self.assertNotIn('RUNTIME_STORE_MODULE = "_blender_ime_fix_runtime_store"', source)

    def test_developer_panel_is_controlled_by_addon_preferences(self):
        source = CORE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(CORE_PATH))
        class_names = {
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        }

        self.assertIn("IME_PT_panel", class_names)
        self.assertIn("IME_AddonPreferences", class_names)
        self.assertIn("show_developer_panel: bpy.props.BoolProperty(", source)
        self.assertIn("default=False", source)
        self.assertIn("update=_update_developer_panel", source)
        self.assertIn("_classes = (IME_OT_action, IME_AddonPreferences)", source)
        self.assertNotIn("_classes = (IME_OT_action, IME_PT_panel)", source)
        self.assertIn("_set_developer_panel_visible(True)", source)

    def test_gpl_license_is_declared_in_shipped_sources(self):
        license_text = LICENSE_PATH.read_text(encoding="utf-8")
        self.assertIn("GNU GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3, 29 June 2007", license_text)
        for source_path in (ADDON_INIT, CORE_PATH):
            with self.subTest(source_path=source_path):
                self.assertTrue(
                    source_path.read_text(encoding="utf-8").startswith(
                        "# SPDX-License-Identifier: GPL-3.0-or-later"
                    )
                )

    def test_builder_creates_installable_top_level_package(self):
        builder = load_build_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path, digest = builder.build(temporary_directory)
            self.assertEqual(archive_path.name, "blender_ime_input_fix-v0.1.0.zip")
            self.assertEqual(len(digest), 64)
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(
                    archive.namelist(),
                    [
                        "blender_ime_fix/__init__.py",
                        "blender_ime_fix/core.py",
                        "blender_ime_fix/LICENSE",
                    ],
                )
                self.assertEqual(
                    archive.read("blender_ime_fix/LICENSE"),
                    LICENSE_PATH.read_bytes(),
                )


if __name__ == "__main__":
    unittest.main()
