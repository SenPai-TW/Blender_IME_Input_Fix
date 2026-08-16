"""
Blender IME 修復 PoC v20（攔截名單版 — 最小侵入性）
=====================================================
歷史測試結論：
  - 重複字元（從 v18 測試確認）：@#%&*+{}| 和九宮格數字 0-9
  - 不重複字元：!$^()_英文字母 []':"<>? 等等

v20 策略：
  只攔截「已知會重複」的字元，其他全部正常通過。
  這是最安全、最小侵入性的方法。

  攔截名單 INTERCEPT_CHARS = @#%&*+{}|0123456789
  - 名單內的 ASCII → 吃掉 WM_IME_COMPOSITION + CallWindowProcW(WM_CHAR) 注入
  - 名單外的 ASCII → 正常通過 GHOST 處理
  - CJK → 永遠正常通過
"""

import ctypes
import ctypes.wintypes as wt
import bpy

# ══════════════════════════════════════════════
# Windows API 宣告
# ══════════════════════════════════════════════

user32 = ctypes.windll.user32
imm32  = ctypes.windll.imm32

WM_KEYDOWN              = 0x0100
WM_KEYUP                = 0x0101
WM_CHAR                 = 0x0102
WM_IME_STARTCOMPOSITION = 0x010D
WM_IME_ENDCOMPOSITION   = 0x010E
WM_IME_COMPOSITION      = 0x010F
WM_IME_CHAR             = 0x0286
WM_IME_NOTIFY           = 0x0282

GCS_COMPSTR   = 0x0008
GCS_RESULTSTR = 0x0800
GWLP_WNDPROC  = -4

MAPVK_VK_TO_VSC = 0

MSG_NAMES = {
    WM_KEYDOWN:              'WM_KEYDOWN',
    WM_KEYUP:                'WM_KEYUP',
    WM_CHAR:                 'WM_CHAR',
    WM_IME_STARTCOMPOSITION: 'IME_START',
    WM_IME_ENDCOMPOSITION:   'IME_END',
    WM_IME_COMPOSITION:      'IME_COMP',
    WM_IME_CHAR:             'WM_IME_CHAR',
    WM_IME_NOTIFY:           'IME_NOTIFY',
}

# ══════════════════════════════════════════════
# 攔截名單：只有這些 ASCII 字元會被去重
# ══════════════════════════════════════════════
# 來源：v18 使用者測試確認這些字元在中文輸入下按 Shift 會重複出現
INTERCEPT_CHARS = set('@#%&*+{}|0123456789')

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM
)

user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
user32.SetWindowLongPtrW.restype  = ctypes.c_ssize_t
user32.CallWindowProcW.argtypes   = [ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CallWindowProcW.restype    = ctypes.c_ssize_t
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype  = wt.HWND
user32.VkKeyScanW.argtypes  = [wt.WCHAR]
user32.VkKeyScanW.restype   = ctypes.c_short
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype  = wt.UINT

imm32.ImmGetContext.argtypes        = [wt.HWND]
imm32.ImmGetContext.restype         = ctypes.c_void_p
imm32.ImmReleaseContext.argtypes    = [wt.HWND, ctypes.c_void_p]
imm32.ImmReleaseContext.restype     = ctypes.c_bool
imm32.ImmGetCompositionStringW.argtypes = [
    ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD
]
imm32.ImmGetCompositionStringW.restype = ctypes.c_long


# ══════════════════════════════════════════════
# 狀態管理
# ══════════════════════════════════════════════

class _State:
    installed       = False
    hwnd            = 0
    orig_proc       = 0
    proc_ref        = None

    logs            = []
    max_logs        = 60
    fix_enabled     = True
    intercept_count = 0
    pass_count      = 0
    diag_only       = False

S = _State()


# ══════════════════════════════════════════════
# IME 字串讀取
# ══════════════════════════════════════════════

def _get_result_str(hwnd):
    hIMC = imm32.ImmGetContext(hwnd)
    if not hIMC:
        return ''
    try:
        n = imm32.ImmGetCompositionStringW(hIMC, GCS_RESULTSTR, None, 0)
        if n <= 0:
            return ''
        buf = ctypes.create_unicode_buffer(n // 2 + 1)
        imm32.ImmGetCompositionStringW(hIMC, GCS_RESULTSTR, buf, n + 2)
        return buf.value
    finally:
        imm32.ImmReleaseContext(hwnd, hIMC)


# ══════════════════════════════════════════════
# 核心 WndProc
# ══════════════════════════════════════════════

def _add_log(text):
    S.logs.append(text)
    if len(S.logs) > S.max_logs:
        S.logs.pop(0)


def _make_char_lparam(ch_code):
    """從字元碼構造 WM_CHAR 的 lParam（含 scan code + repeat=1）。"""
    vk_scan = user32.VkKeyScanW(ch_code)
    vk = vk_scan & 0xFF
    if vk == 0xFF:
        return 1
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    return 1 | (scan << 16)


def _wnd_proc(hwnd, msg, wp, lp):

    # ════════════════════════════════════════════
    # 核心修復：處理 ASCII WM_IME_COMPOSITION
    # ════════════════════════════════════════════
    if msg == WM_IME_COMPOSITION and (lp & GCS_RESULTSTR) and S.fix_enabled and not S.diag_only:
        result = _get_result_str(hwnd)
        if result:
            is_cjk = any(ord(c) > 127 for c in result)

            if is_cjk:
                # CJK → 永遠正常通過
                _add_log(f"  [CJK] '{result}'")
                return user32.CallWindowProcW(S.orig_proc, hwnd, msg, wp, lp)

            # ASCII：檢查是否在攔截名單中
            # 分成兩組：需攔截的 和 通過的
            to_intercept = [c for c in result if c in INTERCEPT_CHARS]
            to_pass      = [c for c in result if c not in INTERCEPT_CHARS]

            if to_intercept and not to_pass:
                # ── 全部需要攔截 ──
                _add_log(f"  [FIX] '{''.join(to_intercept)}' → 攔截+注入")
                for ch in to_intercept:
                    char_lp = _make_char_lparam(ord(ch))
                    user32.CallWindowProcW(
                        S.orig_proc, hwnd, WM_CHAR, ord(ch), char_lp
                    )
                S.intercept_count += 1
                remaining = lp & ~GCS_RESULTSTR
                if remaining:
                    return user32.CallWindowProcW(S.orig_proc, hwnd, msg, wp, remaining)
                return 0

            elif to_pass and not to_intercept:
                # ── 全部正常通過 ──
                _add_log(f"  [PASS] '{''.join(to_pass)}' → 正常通過")
                S.pass_count += 1
                return user32.CallWindowProcW(S.orig_proc, hwnd, msg, wp, lp)

            else:
                # ── 混合（極少見）：安全起見，全部通過 ──
                _add_log(f"  [MIX] '{result}' → 安全通過")
                return user32.CallWindowProcW(S.orig_proc, hwnd, msg, wp, lp)

        return user32.CallWindowProcW(S.orig_proc, hwnd, msg, wp, lp)

    # ════════════════════════════════════════════
    # 純診斷模式
    # ════════════════════════════════════════════
    if S.diag_only and msg == WM_IME_COMPOSITION and (lp & GCS_RESULTSTR):
        result = _get_result_str(hwnd)
        if result:
            is_cjk = any(ord(c) > 127 for c in result)
            in_list = [c for c in result if c in INTERCEPT_CHARS]
            _add_log(f"  [DIAG] '{result}' cjk={is_cjk} intercept={in_list}")

    # ════════════════════════════════════════════
    # 診斷記錄（精簡版）
    # ════════════════════════════════════════════
    if msg in MSG_NAMES and msg != WM_IME_NOTIFY:
        name = MSG_NAMES[msg]
        extra = ''
        if msg == WM_CHAR:
            try:   extra = f" '{chr(wp)}'"
            except: extra = f" ({wp:#x})"
        elif msg == WM_KEYDOWN:
            extra = f" VK={wp:#04x}"
        elif msg == WM_IME_CHAR:
            try:   extra = f" '{chr(wp)}'"
            except: extra = f" ({wp:#x})"
        _add_log(f"  {name}{extra}")

    return user32.CallWindowProcW(S.orig_proc, hwnd, msg, wp, lp)


# ══════════════════════════════════════════════
# 安裝 / 卸載
# ══════════════════════════════════════════════

def _install():
    if S.installed:
        _uninstall()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        _add_log("❌ GetForegroundWindow 失敗")
        return False
    cb = WNDPROC(_wnd_proc)
    S.proc_ref = cb
    cb_addr = ctypes.cast(cb, ctypes.c_void_p).value
    orig = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, cb_addr)
    if not orig:
        _add_log(f"❌ SetWindowLongPtrW 失敗 (err={ctypes.GetLastError()})")
        S.proc_ref = None
        return False
    S.hwnd            = hwnd
    S.orig_proc       = orig
    S.installed       = True
    S.intercept_count = 0
    S.pass_count      = 0
    mode = "純診斷" if S.diag_only else "修復"
    _add_log(f"✅ v20 已啟動 (HWND={hwnd:#010x}) 模式={mode}")
    _add_log(f"  攔截名單: {''.join(sorted(INTERCEPT_CHARS))}")
    return True


def _uninstall():
    if not S.installed or not S.orig_proc:
        return
    user32.SetWindowLongPtrW(S.hwnd, GWLP_WNDPROC, S.orig_proc)
    S.installed = False
    S.hwnd      = 0
    S.orig_proc = 0
    S.proc_ref  = None
    _add_log("⏹ 已停止並還原")


# ══════════════════════════════════════════════
# Blender UI 面板
# ══════════════════════════════════════════════

class IME_OT_action(bpy.types.Operator):
    bl_idname = "ime_fix.action"
    bl_label  = "IME Action"
    action: bpy.props.StringProperty()

    def execute(self, context):
        a = self.action
        if   a == 'INSTALL':     _install()
        elif a == 'UNINSTALL':   _uninstall()
        elif a == 'TOGGLE_FIX':  S.fix_enabled = not S.fix_enabled
        elif a == 'TOGGLE_DIAG': S.diag_only   = not S.diag_only
        elif a == 'CLEAR':
            S.logs.clear()
            S.intercept_count = 0
            S.pass_count      = 0
        return {'FINISHED'}


class IME_PT_panel(bpy.types.Panel):
    bl_label       = "IME 修復工具 v20"
    bl_idname      = "IME_PT_fix_panel"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "IME 診斷"

    def draw(self, context):
        layout = self.layout

        # ── 狀態 ──
        box = layout.box()
        if S.installed:
            box.label(text="Hook: 運行中 v20", icon='CHECKMARK')
            box.operator("ime_fix.action", text="停止還原", icon='CANCEL').action = 'UNINSTALL'
        else:
            box.label(text="Hook: 未啟動", icon='X')
            box.operator("ime_fix.action", text="啟動", icon='PLAY').action = 'INSTALL'

        # ── 模式切換 ──
        layout.separator()
        box2 = layout.box()
        icon_fix  = 'CHECKBOX_HLT'      if S.fix_enabled else 'CHECKBOX_DEHLT'
        icon_diag = 'RESTRICT_VIEW_OFF' if S.diag_only   else 'RESTRICT_VIEW_ON'
        box2.operator("ime_fix.action",
                      text=f"修復開關 {'ON' if S.fix_enabled else 'OFF'}",
                      icon=icon_fix).action  = 'TOGGLE_FIX'
        box2.operator("ime_fix.action",
                      text=f"純診斷模式 {'ON' if S.diag_only else 'OFF'}",
                      icon=icon_diag).action = 'TOGGLE_DIAG'

        if S.diag_only:
            box2.label(text="⚠️ 純診斷：不修復，僅記錄", icon='INFO')

        col = box2.column(align=True)
        col.label(text="v20: 只攔截已知重複字元")
        col.label(text=f"  攔截: {''.join(sorted(INTERCEPT_CHARS))}")
        col.label(text="  其他 ASCII/CJK → 正常通過")

        row2 = box2.row(align=True)
        if S.intercept_count > 0:
            row2.label(text=f"去重: {S.intercept_count}", icon='SHIELD')
        if S.pass_count > 0:
            row2.label(text=f"通過: {S.pass_count}", icon='FORWARD')

        # ── 日誌 ──
        layout.separator()
        row = layout.row(align=True)
        row.label(text=f"日誌 ({len(S.logs)} 筆):", icon='TEXT')
        row.operator("ime_fix.action", text="清空", icon='TRASH').action = 'CLEAR'
        box3 = layout.box()
        if not S.logs:
            box3.label(text="（尚無記錄）")
        else:
            col = box3.column(align=True)
            for entry in reversed(S.logs[-20:]):
                col.label(text=entry)


# ══════════════════════════════════════════════
# 註冊
# ══════════════════════════════════════════════

_classes = (IME_OT_action, IME_PT_panel)

def register():
    for cls in _classes:
        try: bpy.utils.register_class(cls)
        except Exception: pass
    _install()

def unregister():
    _uninstall()
    for cls in reversed(_classes):
        try: bpy.utils.unregister_class(cls)
        except Exception: pass

if __name__ == "__main__":
    register()
