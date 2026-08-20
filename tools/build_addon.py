"""Build a deterministic Blender legacy add-on ZIP from the package source."""

import ast
import hashlib
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "blender_ime_fix"
PACKAGE_DIR = ROOT / PACKAGE_NAME
ARCHIVE_MEMBERS = (
    (PACKAGE_DIR / "__init__.py", f"{PACKAGE_NAME}/__init__.py"),
    (PACKAGE_DIR / "core.py", f"{PACKAGE_NAME}/core.py"),
    (ROOT / "LICENSE", f"{PACKAGE_NAME}/LICENSE"),
)


def _addon_version():
    tree = ast.parse(
        (PACKAGE_DIR / "__init__.py").read_text(encoding="utf-8"),
        filename=str(PACKAGE_DIR / "__init__.py"),
    )
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "bl_info"
            for target in node.targets
        ):
            continue
        info = ast.literal_eval(node.value)
        return tuple(info["version"])
    raise RuntimeError("bl_info was not found")


def build(output_dir=None):
    """Create the installable ZIP and return its path and SHA-256."""
    version = ".".join(str(part) for part in _addon_version())
    destination = Path(output_dir) if output_dir else ROOT / "dist"
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / f"blender_ime_input_fix-v{version}.zip"

    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for source, archive_name in ARCHIVE_MEMBERS:
            member = zipfile.ZipInfo(
                archive_name,
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            archive.writestr(member, source.read_bytes())

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    return archive_path, digest


if __name__ == "__main__":
    path, sha256 = build()
    print(path)
    print(f"SHA-256: {sha256}")
