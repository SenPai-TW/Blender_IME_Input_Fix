# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 SenPai-TW

"""Blender add-on entry point for the Windows IME duplicate-input fix."""

bl_info = {
    "name": "Blender IME Input Fix",
    "author": "SenPai-TW",
    "version": (0, 1, 1),
    "blender": (2, 80, 0),
    "location": "3D Viewport > Sidebar > IME 診斷",
    "description": "修正 Windows Microsoft 注音在 Blender 中的重複字元與注音首鍵問題",
    "warning": "Windows only",
    "doc_url": "https://github.com/SenPai-TW/Blender_IME_Input_Fix",
    "tracker_url": "https://github.com/SenPai-TW/Blender_IME_Input_Fix/issues",
    "category": "System",
}


def register():
    """Start the add-on through its single runtime owner."""
    from . import core

    core.register()


def unregister():
    """Stop the runtime and remove every Blender/Win32 registration."""
    from . import core

    core.unregister()
