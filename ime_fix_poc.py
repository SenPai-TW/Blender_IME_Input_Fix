"""
Blender IME 修復 PoC v12（ASCII / CJK 智慧分流修復版）
======================================================
核心診斷與修復機制：
  1. CJK 中文字元（ord > 127）：
     Blender 本身處理中文字串確認時不會重複。直接放行 WM_IME_COMPOSITION，
     確保選字後按下 Enter 字串能正常提交且不消失。
  2. ASCII 字元（英數 0-9, a-z, A-Z 以及 @, #, % 等符號）：
     此類字元會因 IME + DefWindowProc 觸發二次插入造成重複。
     攔截其 WM_IME_COMPOSITION，改由單次 WM_CHAR 重注入，保證只輸入一次。
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

MSG_NAMES = {
    WM_KEYDOWN:              'WM_KEYDOWN',
    WM_KEYUP:                'WM_KEYUP',
    WM_CHAR:                 'WM_CHAR',
    WM_IME_STARTCOMPOSITION: 'IME_START_COMP',
    WM_IME_ENDCOMPOSITION:   'IME_END_COMP',
    WM_IME_COMPOSITION:      'IME_COMPOSITION',
    WM_IME_CHAR:             'WM_IME_CHAR',
    WM_IME_NOTIFY:           'IME_NOTIFY',
}

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM
)

user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
user32.SetWindowLongPtrW.restype  = ctypes.c_ssize_t
user32.CallWindowProcW.argtypes   = [ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CallWindowProcW.restype    = ctypes.c_ssize_t
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype  = wt.HWND
user32.GetKeyState.argtypes         = [ctypes.c_int]
user32.GetKeyState.restype          = ctypes.c_short
user32.PostMessageW.argtypes        = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype         = wt.BOOL

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
    max_logs        = 40
    fix_enabled     = True
    intercept_count = 0
    reinject_count  = 0

S = _State()


# ══════════════════════════════════════════════
# IME 字串讀取
# ══════════════════════════════════════════════

def _get_result_str(hwnd):
    """讀取 IME 已確認的結果字串"""
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


def _get_comp_str(hwnd):
    """讀取 IME 組合中的字串"""
    hIMC = imm32.ImmGetContext(hwnd)
    if not hIMC:
        return ''
    try:
        n = imm32.ImmGetCompositionStringW(hIMC, GCS_COMPSTR, None, 0)
        if n <= 0:
            return ''
        buf = ctypes.create_unicode_buffer(n // 2 + 1)
        imm32.ImmGetCompositionStringW(hIMC, GCS_COMPSTR, buf, n + 2)
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


def _wnd_proc(hwnd, msg, wp, lp):

    # ═══════════════════════════════════════════
    # 智慧修復：分流處理 IME_COMPOSITION (GCS_RESULTSTR)
    # ═══════════════════════════════════════════
    if S.fix_enabled and msg == WM_IME_COMPOSITION and (lp & GCS_RESULTSTR):
        result = _get_result_str(hwnd)

        if result:
            # 檢查是否含有非 ASCII (例如中文字元)
            is_cjk = any(ord(c) > 127 for c in result)

            if is_cjk:
                # 【中文字元】：放行給 Blender 原生處理，確保選字及 Enter 正常提交
                _add_log(f"🀄 [CJK放行] result='{result}'")
                return user32.CallWindowProcW(S.orig_proc, hwnd, msg, wp, lp)
            else:
                # 【ASCII 英數及符號】：攔截並重注入單次 WM_CHAR，徹底消滅重複
                S.intercept_count += 1
                _add_log(f"🛡 [ASCII攔截] result='{result}'")

                for ch in result:
                    user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 0)
                    S.reinject_count += 1

                _add_log(f"💉 [重注入] '{result}'")

                # 如果還有其他旗標（如 COMPSTR 清理），傳遞剩餘旗標
                remaining_flags = lp & ~GCS_RESULTSTR
                if remaining_flags:
                    return user32.CallWindowProcW(S.orig_proc, hwnd, msg, wp, remaining_flags)
                return 0

        return user32.CallWindowProcW(S.orig_proc, hwnd, msg, wp, lp)

    # ═══════════════════════════════════════════
    # 診斷記錄
    # ═══════════════════════════════════════════
    if msg in MSG_NAMES and msg != WM_IME_NOTIFY:
        name = MSG_NAMES[msg]
        extra = ''

        if msg == WM_CHAR:
            try: extra = f" '{chr(wp)}' ({wp:#06x})"
            except Exception: extra = f" ({wp:#06x})"
        elif msg == WM_KEYDOWN:
            extra = f" VK={wp:#04x}"
        elif msg == WM_IME_CHAR:
            try: extra = f" '{chr(wp)}'"
            except Exception: extra = f" ({wp:#06x})"
        elif msg == WM_IME_COMPOSITION:
            parts = []
            if lp & GCS_COMPSTR:
                cs = _get_comp_str(hwnd)
                if cs: parts.append(f"comp='{cs}'")
            if lp & GCS_RESULTSTR:
                rs = _get_result_str(hwnd)
                if rs: parts.append(f"result='{rs}'")
            if parts:
                extra = ' ' + ', '.join(parts)

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

    S.hwnd      = hwnd
    S.orig_proc = orig
    S.installed = True
    S.intercept_count = 0
    S.reinject_count  = 0
    _add_log(f"✅ PoC v12 已啟動 (HWND={hwnd:#010x})")
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
# Blender UI 面板 (3D View N 面板)
# ══════════════════════════════════════════════

class IME_OT_action(bpy.types.Operator):
    bl_idname = "ime_fix.action"
    bl_label  = "IME Action"
    action: bpy.props.StringProperty()

    def execute(self, context):
        a = self.action
        if   a == 'INSTALL':    _install()
        elif a == 'UNINSTALL':  _uninstall()
        elif a == 'TOGGLE_FIX': S.fix_enabled = not S.fix_enabled
        elif a == 'CLEAR':
            S.logs.clear()
            S.intercept_count = 0
            S.reinject_count  = 0
        return {'FINISHED'}


class IME_PT_panel(bpy.types.Panel):
    bl_label       = "IME 修復工具 v12"
    bl_idname      = "IME_PT_fix_panel"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "IME 診斷"

    def draw(self, context):
        layout = self.layout

        # ── 狀態 ──
        box = layout.box()
        if S.installed:
            box.label(text="Hook 狀態: 運行中 (v12 分流版)", icon='CHECKMARK')
            box.operator("ime_fix.action", text="停止並還原", icon='CANCEL').action = 'UNINSTALL'
        else:
            box.label(text="Hook 狀態: 未啟動", icon='X')
            box.operator("ime_fix.action", text="啟動修復", icon='PLAY').action = 'INSTALL'

        # ── 修復開關 ──
        layout.separator()
        box2 = layout.box()
        icon_fix = 'CHECKBOX_HLT' if S.fix_enabled else 'CHECKBOX_DEHLT'
        box2.operator("ime_fix.action", text="ASCII/CJK 智慧分流修復", icon=icon_fix).action = 'TOGGLE_FIX'

        if S.fix_enabled:
            col = box2.column(align=True)
            col.label(text="• ASCII (英數/符號): 攔截並單次重注入")
            col.label(text="• CJK (中文字元): 放行原生提交")

        if S.intercept_count > 0 or S.reinject_count > 0:
            row = box2.row()
            row.label(text=f"ASCII 攔截: {S.intercept_count}", icon='SHIELD')
            row.label(text=f"重注入: {S.reinject_count}", icon='FORWARD')

        # ── 日誌 ──
        layout.separator()
        row = layout.row(align=True)
        row.label(text=f"訊息日誌 ({len(S.logs)} 筆):", icon='TEXT')
        row.operator("ime_fix.action", text="清空", icon='TRASH').action = 'CLEAR'

        box3 = layout.box()
        if not S.logs:
            box3.label(text="（尚無記錄）")
        else:
            col = box3.column(align=True)
            for entry in reversed(S.logs[-15:]):
                col.label(text=entry)


# ══════════════════════════════════════════════
# 註冊
# ══════════════════════════════════════════════

_classes = (IME_OT_action, IME_PT_panel)

def register():
    for cls in _classes:
        try:
            bpy.utils.register_class(cls)
        except Exception:
            pass
    _install()

def unregister():
    _uninstall()
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

if __name__ == "__main__":
    register()
