# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 SenPai-TW

"""
Blender IME Input Fix v24 core.

This module owns the complete runtime: public Win32 API bindings, message
policy, hook lifecycle, and Blender UI classes. The package facade only
delegates ``register()`` and ``unregister()`` here.

歷史測試結論：
  - 重複字元（從 v18-v22 測試確認）：-=@#%&*+{}| 和九宮格數字 0-9
  - 不重複字元：!$^()_英文字母 []':"<>? 等等

v24 策略：
  只攔截「已知會重複」的字元，其他全部正常通過。

  攔截名單 INTERCEPT_CHARS = -=@#%&*+{}|0123456789
  - 名單內的 ASCII → 吃掉 WM_IME_COMPOSITION + DefSubclassProc(WM_CHAR) 注入
  - 名單外的 ASCII → 正常通過 GHOST 處理
  - CJK → 永遠正常通過

  Microsoft 注音首鍵：
  - 台灣中文 Native 模式、未按 Shift 的主鍵盤 1-0/- → 攔截 WM_INPUT KeyDown
  - 數字鍵盤、英數模式、其他輸入語系、KeyUp → 正常通過
  - Win32 查詢失敗 → fail-open，正常通過

  Hook 生命週期：
  - 使用 Windows 公開的 SetWindowSubclass / RemoveWindowSubclass
  - 不讀取 Blender 私有記憶體，不使用版本偏移或自製 DLL
  - 重複啟動不疊加；卸載失敗時保留 callback 並停止再次安裝
  - 自動同步同一 Blender UI 執行緒的新建與關閉視窗
"""

import ctypes
import ctypes.wintypes as wt
import sys
import types
import bpy

# ══════════════════════════════════════════════
# Windows API 宣告
# ══════════════════════════════════════════════

user32   = ctypes.WinDLL("user32", use_last_error=True)
imm32    = ctypes.WinDLL("imm32", use_last_error=True)
comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_NCDESTROY            = 0x0082
WM_INPUT                = 0x00FF
WM_KEYDOWN              = 0x0100
WM_KEYUP                = 0x0101
WM_CHAR                 = 0x0102
WM_SYSKEYUP             = 0x0105
WM_IME_STARTCOMPOSITION = 0x010D
WM_IME_ENDCOMPOSITION   = 0x010E
WM_IME_COMPOSITION      = 0x010F
WM_IME_CHAR             = 0x0286
WM_IME_NOTIFY           = 0x0282

GCS_RESULTSTR = 0x0800

MAPVK_VK_TO_VSC = 0
VK_SHIFT         = 0x10
RID_INPUT        = 0x10000003
RIM_TYPEKEYBOARD = 1
RI_KEY_BREAK      = 0x0001

IME_CMODE_NATIVE       = 0x0001
IME_CMODE_FULLSHAPE    = 0x0008
IME_CMODE_NOCONVERSION = 0x0100

LANGID_ZH_TW = 0x0404
SUBCLASS_ID = 0x494D4532  # ASCII "IME2"；不含 Blender 版本資訊
ADDON_PACKAGE = __package__ or "blender_ime_fix"
# Keep the reload-safe owner inside this add-on's package namespace. This
# survives a module reload without claiming a process-global top-level name.
RUNTIME_STORE_MODULE = ADDON_PACKAGE + "._runtime_store"
SYNC_INTERVAL_SECONDS = 1.0
BOPOMOFO_TOP_ROW_VKEYS = {
    0x30, 0x31, 0x32, 0x33, 0x34,
    0x35, 0x36, 0x37, 0x38, 0x39,
    0xBD,
}

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
# 來源：v18-v20 使用者測試；v21 加入 =，v22 加入數字鍵盤 -
INTERCEPT_CHARS = set('-=@#%&*+{}|0123456789')


def _should_suppress_bopomofo_raw(
    vkey,
    key_down,
    ime_native,
    input_lang_id,
    shift_down=False,
):
    """Pure policy: keep Bopomofo-owned top-row KeyDown out of GHOST Raw Input."""
    return bool(
        key_down
        and ime_native
        and input_lang_id == LANGID_ZH_TW
        and vkey in BOPOMOFO_TOP_ROW_VKEYS
        and not shift_down
    )


class _RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ('dwType', wt.DWORD),
        ('dwSize', wt.DWORD),
        ('hDevice', wt.HANDLE),
        ('wParam', wt.WPARAM),
    ]


class _RAWMOUSEBUTTONS(ctypes.Structure):
    _fields_ = [
        ('usButtonFlags', ctypes.c_ushort),
        ('usButtonData', ctypes.c_ushort),
    ]


class _RAWMOUSEBUTTONUNION(ctypes.Union):
    _fields_ = [
        ('ulButtons', wt.ULONG),
        ('buttons', _RAWMOUSEBUTTONS),
    ]


class _RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ('usFlags', ctypes.c_ushort),
        ('button_data', _RAWMOUSEBUTTONUNION),
        ('ulRawButtons', wt.ULONG),
        ('lLastX', wt.LONG),
        ('lLastY', wt.LONG),
        ('ulExtraInformation', wt.ULONG),
    ]


class _RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ('MakeCode', ctypes.c_ushort),
        ('Flags', ctypes.c_ushort),
        ('Reserved', ctypes.c_ushort),
        ('VKey', ctypes.c_ushort),
        ('Message', wt.UINT),
        ('ExtraInformation', wt.ULONG),
    ]


class _RAWHID(ctypes.Structure):
    _fields_ = [
        ('dwSizeHid', wt.DWORD),
        ('dwCount', wt.DWORD),
        ('bRawData', ctypes.c_ubyte * 1),
    ]


class _RAWINPUTDATA(ctypes.Union):
    _fields_ = [
        ('mouse', _RAWMOUSE),
        ('keyboard', _RAWKEYBOARD),
        ('hid', _RAWHID),
    ]


class _RAWINPUT(ctypes.Structure):
    _fields_ = [
        ('header', _RAWINPUTHEADER),
        ('data', _RAWINPUTDATA),
    ]

UINT_PTR = ctypes.c_size_t
DWORD_PTR = ctypes.c_size_t

SUBCLASSPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wt.HWND,
    wt.UINT,
    wt.WPARAM,
    wt.LPARAM,
    UINT_PTR,
    DWORD_PTR,
)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.VkKeyScanW.argtypes  = [wt.WCHAR]
user32.VkKeyScanW.restype   = ctypes.c_short
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype  = wt.UINT
user32.GetRawInputData.argtypes = [
    wt.HANDLE, wt.UINT, ctypes.c_void_p, ctypes.POINTER(wt.UINT), wt.UINT
]
user32.GetRawInputData.restype = wt.UINT
user32.GetKeyboardLayout.argtypes = [wt.DWORD]
user32.GetKeyboardLayout.restype = ctypes.c_void_p
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = ctypes.c_short
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL

kernel32.GetCurrentProcessId.argtypes = []
kernel32.GetCurrentProcessId.restype = wt.DWORD
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wt.DWORD

comctl32.SetWindowSubclass.argtypes = [
    wt.HWND, ctypes.c_void_p, UINT_PTR, DWORD_PTR
]
comctl32.SetWindowSubclass.restype = wt.BOOL
comctl32.RemoveWindowSubclass.argtypes = [
    wt.HWND, ctypes.c_void_p, UINT_PTR
]
comctl32.RemoveWindowSubclass.restype = wt.BOOL
comctl32.DefSubclassProc.argtypes = [
    wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM
]
comctl32.DefSubclassProc.restype = ctypes.c_ssize_t

imm32.ImmGetContext.argtypes        = [wt.HWND]
imm32.ImmGetContext.restype         = ctypes.c_void_p
imm32.ImmReleaseContext.argtypes    = [wt.HWND, ctypes.c_void_p]
imm32.ImmReleaseContext.restype     = ctypes.c_bool
imm32.ImmGetCompositionStringW.argtypes = [
    ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD
]
imm32.ImmGetCompositionStringW.restype = ctypes.c_long
imm32.ImmGetOpenStatus.argtypes = [ctypes.c_void_p]
imm32.ImmGetOpenStatus.restype = wt.BOOL
imm32.ImmGetConversionStatus.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD)
]
imm32.ImmGetConversionStatus.restype = wt.BOOL


# ══════════════════════════════════════════════
# 狀態管理
# ══════════════════════════════════════════════

class _State:
    def __init__(self):
        self.installed       = False
        self.hooked_windows  = ()
        self.hook_phase      = "STOPPED"
        self.last_hook_error = ""

        self.logs            = []
        self.max_logs        = 60
        self.fix_enabled     = True
        self.intercept_count = 0
        self.bopomofo_count  = 0
        self.pass_count      = 0
        self.diag_only       = False


class _HookManager:
    """Own one callback and its SetWindowSubclass registrations."""

    def __init__(self, adapter, callback, subclass_id):
        self._adapter = adapter
        self._callback = callback
        self._subclass_id = subclass_id
        self._hooked = set()
        self.phase = "STOPPED"
        self.last_error = ""

    @property
    def hooked_windows(self):
        return tuple(sorted(self._hooked))

    @property
    def can_release(self):
        return not self._hooked

    def start(self):
        if self.phase == "DEGRADED":
            return False
        return self.reconcile()

    def reconcile(self):
        if self.phase == "DEGRADED":
            return False

        self.last_error = ""
        try:
            targets = set(self._adapter.target_windows())
        except Exception as exc:
            self.last_error = f"列舉 Blender 視窗失敗: {exc}"
            self.phase = "RUNNING" if self._hooked else "WAITING"
            return False

        remove_failures = []
        for hwnd in tuple(self._hooked - targets):
            if not self._adapter.is_window(hwnd):
                self._hooked.discard(hwnd)
            elif self._adapter.remove(hwnd, self._callback, self._subclass_id):
                self._hooked.discard(hwnd)
            else:
                remove_failures.append(hwnd)

        if remove_failures:
            self.last_error = (
                "無法移除視窗 Hook: "
                + ", ".join(f"{hwnd:#x}" for hwnd in remove_failures)
            )
            self.phase = "DEGRADED"
            return False

        install_failures = []
        for hwnd in sorted(targets - self._hooked):
            if self._adapter.install(hwnd, self._callback, self._subclass_id):
                self._hooked.add(hwnd)
            else:
                install_failures.append(hwnd)

        if install_failures:
            self.last_error = (
                "無法安裝視窗 Hook: "
                + ", ".join(f"{hwnd:#x}" for hwnd in install_failures)
            )

        self.phase = "RUNNING" if self._hooked else "WAITING"
        return not install_failures

    def stop(self):
        failures = []
        for hwnd in tuple(self._hooked):
            if not self._adapter.is_window(hwnd):
                self._hooked.discard(hwnd)
            elif self._adapter.remove(hwnd, self._callback, self._subclass_id):
                self._hooked.discard(hwnd)
            else:
                failures.append(hwnd)

        if failures:
            self.last_error = (
                "卸載失敗，callback 必須保留: "
                + ", ".join(f"{hwnd:#x}" for hwnd in failures)
            )
            self.phase = "DEGRADED"
            return False

        self.last_error = ""
        self.phase = "STOPPED"
        return True

    def window_destroyed(self, hwnd):
        self._hooked.discard(hwnd)
        if self.phase not in {"STOPPED", "DEGRADED"}:
            self.phase = "RUNNING" if self._hooked else "WAITING"

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


def _read_raw_keyboard(raw_input_handle):
    """Return (virtual_key, key_down), or None on non-keyboard/error."""
    try:
        raw = _RAWINPUT()
        raw_size = wt.UINT(ctypes.sizeof(raw))
        copied = user32.GetRawInputData(
            wt.HANDLE(raw_input_handle),
            RID_INPUT,
            ctypes.byref(raw),
            ctypes.byref(raw_size),
            ctypes.sizeof(_RAWINPUTHEADER),
        )
        if copied == 0xFFFFFFFF or raw.header.dwType != RIM_TYPEKEYBOARD:
            return None

        keyboard = raw.data.keyboard
        key_down = not (keyboard.Flags & RI_KEY_BREAK)
        key_down = key_down and keyboard.Message not in (WM_KEYUP, WM_SYSKEYUP)
        return int(keyboard.VKey), bool(key_down)
    except Exception:
        return None


def _is_shift_down():
    """Return the queued Shift state; fail open if Win32 state cannot be read."""
    try:
        return bool(user32.GetKeyState(VK_SHIFT) & 0x8000)
    except Exception:
        return True


def _get_ime_native_state(hwnd):
    """Return (native_mode, LANGID); fail-open callers treat false/zero as pass."""
    try:
        keyboard_layout = user32.GetKeyboardLayout(0)
        input_lang_id = int(keyboard_layout or 0) & 0xFFFF

        hIMC = imm32.ImmGetContext(hwnd)
        if not hIMC:
            return False, input_lang_id
        try:
            if not imm32.ImmGetOpenStatus(hIMC):
                return False, input_lang_id
            conversion = wt.DWORD(0)
            sentence = wt.DWORD(0)
            if not imm32.ImmGetConversionStatus(
                hIMC, ctypes.byref(conversion), ctypes.byref(sentence)
            ):
                return False, input_lang_id
            native = not (conversion.value & IME_CMODE_NOCONVERSION)
            native = native and bool(
                conversion.value & (IME_CMODE_NATIVE | IME_CMODE_FULLSHAPE)
            )
            return bool(native), input_lang_id
        finally:
            imm32.ImmReleaseContext(hwnd, hIMC)
    except Exception:
        return False, 0


# ══════════════════════════════════════════════
# 核心 WndProc
# ══════════════════════════════════════════════

def _add_log(state, text):
    state.logs.append(text)
    if len(state.logs) > state.max_logs:
        state.logs.pop(0)


def _make_char_lparam(character):
    """從單一 Unicode 字元構造 WM_CHAR lParam（scan code + repeat=1）。"""
    vk_scan = user32.VkKeyScanW(character)
    vk = vk_scan & 0xFF
    if vk == 0xFF:
        return 1
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    return 1 | (scan << 16)


def _handle_message(state, call_next, hwnd, msg, wp, lp):

    # ════════════════════════════════════════════
    # Microsoft 注音：阻止 GHOST Raw Input 插入未加 Shift 的首鍵 1-0/-
    # ════════════════════════════════════════════
    if msg == WM_INPUT and (state.fix_enabled or state.diag_only):
        raw_key = _read_raw_keyboard(lp)
        if raw_key:
            vkey, key_down = raw_key
            ime_native, input_lang_id = _get_ime_native_state(hwnd)
            if _should_suppress_bopomofo_raw(
                vkey,
                key_down,
                ime_native,
                input_lang_id,
                shift_down=_is_shift_down(),
            ):
                if state.diag_only:
                    _add_log(
                        state,
                        f"  [BPMF-DIAG] WM_INPUT VK={vkey:#04x} → 通過",
                    )
                else:
                    state.bopomofo_count += 1
                    _add_log(
                        state,
                        f"  [BPMF-FIX] WM_INPUT VK={vkey:#04x} → 攔截",
                    )
                    # 前景 WM_INPUT 必須交給 DefWindowProc 做系統清理，
                    # 但不能交給 Blender/GHOST 建立文字按鍵事件。
                    return user32.DefWindowProcW(hwnd, msg, wp, lp)

    # ════════════════════════════════════════════
    # 核心修復：處理 ASCII WM_IME_COMPOSITION
    # ════════════════════════════════════════════
    if (
        msg == WM_IME_COMPOSITION
        and (lp & GCS_RESULTSTR)
        and state.fix_enabled
        and not state.diag_only
    ):
        result = _get_result_str(hwnd)
        if result:
            is_cjk = any(ord(c) > 127 for c in result)

            if is_cjk:
                # CJK → 永遠正常通過
                _add_log(state, f"  [CJK] '{result}'")
                return call_next(hwnd, msg, wp, lp)

            # ASCII：檢查是否在攔截名單中
            # 分成兩組：需攔截的 和 通過的
            to_intercept = [c for c in result if c in INTERCEPT_CHARS]
            to_pass      = [c for c in result if c not in INTERCEPT_CHARS]

            if to_intercept and not to_pass:
                # ── 全部需要攔截 ──
                _add_log(
                    state,
                    f"  [FIX] '{''.join(to_intercept)}' → 攔截+注入",
                )
                for ch in to_intercept:
                    char_lp = _make_char_lparam(ch)
                    call_next(hwnd, WM_CHAR, ord(ch), char_lp)
                state.intercept_count += 1
                remaining = lp & ~GCS_RESULTSTR
                if remaining:
                    return call_next(hwnd, msg, wp, remaining)
                return 0

            elif to_pass and not to_intercept:
                # ── 全部正常通過 ──
                _add_log(state, f"  [PASS] '{''.join(to_pass)}' → 正常通過")
                state.pass_count += 1
                return call_next(hwnd, msg, wp, lp)

            else:
                # ── 混合（極少見）：安全起見，全部通過 ──
                _add_log(state, f"  [MIX] '{result}' → 安全通過")
                return call_next(hwnd, msg, wp, lp)

        return call_next(hwnd, msg, wp, lp)

    # ════════════════════════════════════════════
    # 純診斷模式
    # ════════════════════════════════════════════
    if state.diag_only and msg == WM_IME_COMPOSITION and (lp & GCS_RESULTSTR):
        result = _get_result_str(hwnd)
        if result:
            is_cjk = any(ord(c) > 127 for c in result)
            in_list = [c for c in result if c in INTERCEPT_CHARS]
            _add_log(
                state,
                f"  [DIAG] '{result}' cjk={is_cjk} intercept={in_list}",
            )

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
        _add_log(state, f"  {name}{extra}")

    return call_next(hwnd, msg, wp, lp)


# ══════════════════════════════════════════════
# 安裝 / 卸載
# ══════════════════════════════════════════════

class _Win32HookAdapter:
    """Public Win32 adapter; contains no Blender memory offsets."""

    def target_windows(self):
        process_id = int(kernel32.GetCurrentProcessId())
        thread_id = int(kernel32.GetCurrentThreadId())
        windows = []

        @WNDENUMPROC
        def collect(hwnd, _lparam):
            owner_process = wt.DWORD(0)
            owner_thread = int(
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_process))
            )
            if int(owner_process.value) != process_id or owner_thread != thread_id:
                return True

            class_name = ctypes.create_unicode_buffer(128)
            if user32.GetClassNameW(hwnd, class_name, len(class_name)) <= 0:
                return True
            if class_name.value.startswith("GHOST_WindowClass"):
                windows.append(int(hwnd))
            return True

        ctypes.set_last_error(0)
        if not user32.EnumWindows(collect, 0):
            error = ctypes.get_last_error()
            if error:
                raise OSError(error, "EnumWindows failed")
        return windows

    def is_window(self, hwnd):
        return bool(user32.IsWindow(wt.HWND(hwnd)))

    def install(self, hwnd, callback, subclass_id):
        ctypes.set_last_error(0)
        return bool(
            comctl32.SetWindowSubclass(
                wt.HWND(hwnd),
                ctypes.cast(callback, ctypes.c_void_p),
                UINT_PTR(subclass_id),
                DWORD_PTR(0),
            )
        )

    def remove(self, hwnd, callback, subclass_id):
        ctypes.set_last_error(0)
        return bool(
            comctl32.RemoveWindowSubclass(
                wt.HWND(hwnd),
                ctypes.cast(callback, ctypes.c_void_p),
                UINT_PTR(subclass_id),
            )
        )

    def call_next(self, hwnd, msg, wp, lp):
        return comctl32.DefSubclassProc(hwnd, msg, wp, lp)


class _RuntimeInstance:
    """Own Blender registration, timer, callback, and all hooked HWNDs."""

    def __init__(self, state, classes):
        self.state = state
        self.classes = tuple(classes)
        self.registered_classes = []
        self.adapter = _Win32HookAdapter()
        self.message_handler = _handle_message
        self.callback_ref = SUBCLASSPROC(self._subclass_proc)
        self.hooks = _HookManager(self.adapter, self.callback_ref, SUBCLASS_ID)
        self.timer_ref = self._timer_tick
        self.timer_registered = False
        self.stopping = False

    def _sync_public_state(self):
        self.state.hook_phase = self.hooks.phase
        self.state.hooked_windows = self.hooks.hooked_windows
        self.state.last_hook_error = self.hooks.last_error
        self.state.installed = bool(self.hooks.hooked_windows)

    def _subclass_proc(self, hwnd, msg, wp, lp, subclass_id, _ref_data):
        try:
            if int(subclass_id) != SUBCLASS_ID:
                return self.adapter.call_next(hwnd, msg, wp, lp)
            return self.message_handler(
                self.state,
                self.adapter.call_next,
                hwnd,
                msg,
                wp,
                lp,
            )
        except BaseException as exc:
            # ctypes callback 絕不能讓 Python 例外穿越 Win32 邊界。
            try:
                _add_log(
                    self.state,
                    f"⚠️ Hook callback 失敗，已 fail-open: {type(exc).__name__}",
                )
            except BaseException:
                pass
            try:
                return self.adapter.call_next(hwnd, msg, wp, lp)
            except BaseException:
                return 0
        finally:
            if msg == WM_NCDESTROY:
                try:
                    self.hooks.window_destroyed(int(hwnd))
                    self._sync_public_state()
                except BaseException:
                    pass

    def _timer_tick(self):
        if self.stopping:
            self.timer_registered = False
            return None
        if self.hooks.phase == "DEGRADED":
            self._sync_public_state()
            self.timer_registered = False
            return None
        self.hooks.reconcile()
        self._sync_public_state()
        return SYNC_INTERVAL_SECONDS

    def _start_timer(self):
        if self.timer_registered:
            return not self.stopping
        self.stopping = False
        try:
            bpy.app.timers.register(
                self.timer_ref,
                first_interval=SYNC_INTERVAL_SECONDS,
                persistent=True,
            )
            self.timer_registered = True
            return True
        except TypeError:
            # 舊版 Blender 若尚未提供 persistent 參數，仍可基本運作。
            bpy.app.timers.register(
                self.timer_ref,
                first_interval=SYNC_INTERVAL_SECONDS,
            )
            self.timer_registered = True
            return True
        except Exception as exc:
            _add_log(self.state, f"⚠️ 視窗同步計時器啟動失敗: {exc}")
            return False

    def _stop_timer(self):
        self.stopping = True
        if not self.timer_registered:
            return True
        try:
            is_registered = getattr(bpy.app.timers, "is_registered", None)
            if is_registered is None or is_registered(self.timer_ref):
                bpy.app.timers.unregister(self.timer_ref)
            self.timer_registered = False
            return True
        except Exception as exc:
            _add_log(self.state, f"⚠️ 視窗同步計時器停止失敗: {exc}")
            return False

    def start(self):
        if self.hooks.phase == "DEGRADED":
            _add_log(self.state, "❌ Hook 所有權不明，請重開 Blender")
            return False

        was_stopped = self.hooks.phase == "STOPPED"
        hook_ok = self.hooks.start()
        timer_ok = self._start_timer()
        self._sync_public_state()

        if was_stopped:
            self.state.intercept_count = 0
            self.state.bopomofo_count = 0
            self.state.pass_count = 0
        mode = "純診斷" if self.state.diag_only else "修復"
        if hook_ok:
            _add_log(
                self.state,
                f"✅ v24 已啟動 ({len(self.hooks.hooked_windows)} 個視窗) 模式={mode}",
            )
            _add_log(
                self.state,
                f"  攔截名單: {''.join(sorted(INTERCEPT_CHARS))}",
            )
        else:
            _add_log(self.state, f"❌ {self.hooks.last_error}")
        return hook_ok and timer_ok

    def stop(self):
        timer_stopped = self._stop_timer()
        stopped = self.hooks.stop()
        self._sync_public_state()
        if stopped and timer_stopped:
            _add_log(self.state, "⏹ 已停止並移除全部 Hook")
            return True
        if not stopped:
            _add_log(self.state, f"❌ {self.hooks.last_error}")
            _add_log(self.state, "⚠️ callback 已保留；請重開 Blender")
        if not timer_stopped:
            self.state.last_hook_error = "視窗同步計時器尚未停止"
            _add_log(self.state, "⚠️ 計時器將在下次 tick 自動停止")
        return False

    def shutdown(self):
        if not self.stop():
            return False
        for cls in reversed(self.registered_classes):
            bpy.utils.unregister_class(cls)
        self.registered_classes.clear()
        return True


def _runtime_store():
    store = sys.modules.get(RUNTIME_STORE_MODULE)
    if store is None:
        store = types.ModuleType(RUNTIME_STORE_MODULE)
        store.instance = None
        sys.modules[RUNTIME_STORE_MODULE] = store
    return store


def _replace_runtime(store, new_instance):
    """Replace the process runtime only after the previous owner shuts down."""
    previous = getattr(store, "instance", None)
    if previous is new_instance:
        return True
    if previous is not None and not previous.shutdown():
        return False
    store.instance = new_instance
    return True


def _current_instance():
    return getattr(_runtime_store(), "instance", None)


def _current_state():
    instance = _current_instance()
    return instance.state if instance is not None else S


def _set_developer_panel_visible(visible):
    """Register or remove the diagnostics panel without touching the hook."""
    instance = _current_instance()
    if instance is None:
        return False

    registered = IME_PT_panel in instance.registered_classes
    if visible and not registered:
        bpy.utils.register_class(IME_PT_panel)
        instance.registered_classes.append(IME_PT_panel)
    elif not visible and registered:
        bpy.utils.unregister_class(IME_PT_panel)
        instance.registered_classes.remove(IME_PT_panel)
    return True


def _update_developer_panel(preferences, _context):
    """Apply the Add-on Preferences toggle immediately."""
    if not _set_developer_panel_visible(preferences.show_developer_panel):
        _add_log(_current_state(), "⚠️ Runtime 尚未就緒，無法切換開發面板")


def _developer_panel_preference_enabled():
    """Read the persisted preference after its RNA class is registered."""
    addon = bpy.context.preferences.addons.get(ADDON_PACKAGE)
    return bool(addon and addon.preferences.show_developer_panel)


def _install():
    instance = _current_instance()
    if instance is None:
        _add_log(S, "❌ Add-on runtime 尚未註冊")
        return False
    return instance.start()


def _uninstall():
    instance = _current_instance()
    if instance is None:
        return True
    return instance.stop()


# ══════════════════════════════════════════════
# Blender UI 面板
# ══════════════════════════════════════════════

class IME_OT_action(bpy.types.Operator):
    bl_idname = "ime_fix.action"
    bl_label  = "IME Action"
    action: bpy.props.StringProperty()

    def execute(self, context):
        state = _current_state()
        a = self.action
        if a == 'INSTALL':
            if not _install():
                self.report({'ERROR'}, state.last_hook_error or "Hook 啟動失敗")
                return {'CANCELLED'}
        elif a == 'UNINSTALL':
            if not _uninstall():
                self.report({'ERROR'}, "Hook 卸載失敗，請重開 Blender")
                return {'CANCELLED'}
        elif a == 'TOGGLE_FIX':
            state.fix_enabled = not state.fix_enabled
        elif a == 'TOGGLE_DIAG':
            state.diag_only = not state.diag_only
        elif a == 'CLEAR':
            state.logs.clear()
            state.intercept_count = 0
            state.bopomofo_count  = 0
            state.pass_count      = 0
        return {'FINISHED'}


class IME_PT_panel(bpy.types.Panel):
    bl_label       = "IME 修復工具 v24"
    bl_idname      = "IME_PT_fix_panel"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "IME 診斷"

    def draw(self, context):
        layout = self.layout
        state = _current_state()

        # ── 狀態 ──
        box = layout.box()
        if state.hook_phase == "DEGRADED":
            box.label(text="Hook: 卸載失敗，請重開 Blender", icon='ERROR')
            if state.last_hook_error:
                box.label(text=state.last_hook_error, icon='INFO')
        elif state.installed:
            box.label(
                text=f"Hook: 運行中 v24 ({len(state.hooked_windows)} 視窗)",
                icon='CHECKMARK',
            )
            box.operator("ime_fix.action", text="停止還原", icon='CANCEL').action = 'UNINSTALL'
        elif state.hook_phase == "WAITING":
            box.label(text="Hook: 等待 Blender 視窗", icon='TIME')
            box.operator("ime_fix.action", text="停止", icon='CANCEL').action = 'UNINSTALL'
        else:
            box.label(text="Hook: 未啟動", icon='X')
            box.operator("ime_fix.action", text="啟動", icon='PLAY').action = 'INSTALL'

        # ── 模式切換 ──
        layout.separator()
        box2 = layout.box()
        icon_fix  = 'CHECKBOX_HLT'      if state.fix_enabled else 'CHECKBOX_DEHLT'
        icon_diag = 'RESTRICT_VIEW_OFF' if state.diag_only   else 'RESTRICT_VIEW_ON'
        box2.operator("ime_fix.action",
                      text=f"修復開關 {'ON' if state.fix_enabled else 'OFF'}",
                      icon=icon_fix).action  = 'TOGGLE_FIX'
        box2.operator("ime_fix.action",
                      text=f"純診斷模式 {'ON' if state.diag_only else 'OFF'}",
                      icon=icon_diag).action = 'TOGGLE_DIAG'

        if state.diag_only:
            box2.label(text="⚠️ 純診斷：不修復，僅記錄", icon='INFO')

        col = box2.column(align=True)
        col.label(text="v24: 安全 Hook + 字元去重 + 注音首鍵")
        col.label(text=f"  攔截: {''.join(sorted(INTERCEPT_CHARS))}")
        col.label(text="  注音主鍵盤未按 Shift 的 1-0/- → Raw Input 攔截")
        col.label(text="  其他 ASCII/CJK → 正常通過")
        col.label(text="  不使用 Blender 私有記憶體或版本 DLL")

        row2 = box2.row(align=True)
        if state.intercept_count > 0:
            row2.label(text=f"去重: {state.intercept_count}", icon='SHIELD')
        if state.bopomofo_count > 0:
            row2.label(text=f"注音: {state.bopomofo_count}", icon='FONT_DATA')
        if state.pass_count > 0:
            row2.label(text=f"通過: {state.pass_count}", icon='FORWARD')

        # ── 日誌 ──
        layout.separator()
        row = layout.row(align=True)
        row.label(text=f"日誌 ({len(state.logs)} 筆):", icon='TEXT')
        row.operator("ime_fix.action", text="清空", icon='TRASH').action = 'CLEAR'
        box3 = layout.box()
        if not state.logs:
            box3.label(text="（尚無記錄）")
        else:
            col = box3.column(align=True)
            for entry in reversed(state.logs[-20:]):
                col.label(text=entry)


class IME_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_PACKAGE

    show_developer_panel: bpy.props.BoolProperty(
        name="顯示開發者診斷面板",
        description="顯示 3D Viewport 的 IME 診斷面板、Hook 狀態與事件日誌",
        default=False,
        update=_update_developer_panel,
    )

    def draw(self, _context):
        self.layout.prop(self, "show_developer_panel")


# ══════════════════════════════════════════════
# 註冊
# ══════════════════════════════════════════════

_classes = (IME_OT_action, IME_AddonPreferences)

def register():
    store = _runtime_store()
    instance = _RuntimeInstance(S, _classes)
    if not _replace_runtime(store, instance):
        raise RuntimeError(
            "舊 Hook 無法安全卸載；已保留 callback，請重開 Blender"
        )
    try:
        for cls in _classes:
            bpy.utils.register_class(cls)
            instance.registered_classes.append(cls)
        if _developer_panel_preference_enabled():
            if not _set_developer_panel_visible(True):
                raise RuntimeError("無法依 Add-on 設定啟用開發者診斷面板")
        if not instance.start():
            raise RuntimeError(
                instance.state.last_hook_error or "Hook 或視窗同步計時器啟動失敗"
            )
    except Exception:
        if instance.shutdown():
            store.instance = None
        raise

def unregister():
    store = _runtime_store()
    instance = getattr(store, "instance", None)
    if instance is None:
        return
    if not instance.shutdown():
        raise RuntimeError(
            "Hook 無法安全卸載；callback 已保留，請完整重開 Blender"
        )
    store.instance = None
