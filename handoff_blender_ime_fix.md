# Handoff: Blender 中文輸入法重複字元修復專案 (Pure Python + Win32 API)

## 1. 專案目標與背景
* **問題描述**：在 Windows 平台使用 Blender (2.8+ / 4.x / 5.2) 時，若切換為微軟中文輸入法（注音 / 拼音），輸入數字（0-9）、英文字母（a-z, A-Z）或部分符號（@, #, % 等）時，文字框會出現**重複字元**（如按 `1` 輸出 `11`，按 `D` 輸出 `DD`）。
* **核心約束與目標**：
  * **純 Python + ctypes 實作**：完全不依賴外部編譯的 C/C++ DLL，達成零編譯依賴、跨 Blender 版本通用。
  * 支援常規文字輸入框（屬性面板、Outliner 物件重命名、3D 文字、VSE 等）。

---

## 2. 關鍵技術發現與根本原因 (Root Cause)

經過多輪 Win32 訊息診斷與實際測試，確認重複字元的傳遞鏈如下：
1. **輸入法接管按鍵**：使用者按下鍵盤（如 `D`），Windows 送出 `WM_KEYDOWN (VK_PROCESSKEY / 0xE5)`。
2. **IME 組合與提交**：輸入法發送 `WM_IME_STARTCOMPOSITION` ➔ `WM_IME_COMPOSITION (含 GCS_RESULTSTR='D')` ➔ `WM_IME_ENDCOMPOSITION`。
3. **雙重插入點**：
   * **第 1 次插入**：Blender 收到 `WM_IME_COMPOSITION`，從 `GCS_RESULTSTR` 讀出字元並插入文字框。
   * **第 2 次插入**：若訊息繼續傳給 Windows 預設視窗處理器（`DefWindowProcW`），`DefWindowProcW` 會自動將結果轉為 `WM_CHAR`，Blender 收到後再次插入。
   * 兩者相加導致「打 1 變 11」。
4. **CJK（中文）與 ASCII 的差異**：
   * **中文字元（ord > 127）**：Blender 原生處理 `WM_IME_COMPOSITION` 提交不會重複，且依賴該訊息完成輸入法緩衝區提交。
   * **ASCII 字元（ord <= 127）**：才會觸發雙重插入問題。

---

## 3. 現有程式碼與實作進度

* **主要程式碼位置**：
  * [ime_fix_poc.py](file:///d:/Codex/Workspace/Blender相關開發/ime_fix_poc.py)
* **當前最新版本**：**v12（ASCII / CJK 智慧分流修復版）**

### 架構機制：
* **Hook 機制**：透過 `user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, cb)` 進行 Window Subclassing（64-bit 安全轉型 `ctypes.cast(cb, ctypes.c_void_p).value`）。
* **UI 介面**：在 3D Viewport 側邊欄（<kbd>N</kbd> 面板 ➔ **「IME 診斷」** 分頁）提供即時狀態監控、啟用/停用開關與即時訊息記錄列表。
* **v12 核心修復邏輯**：
  ```python
  if S.fix_enabled and msg == WM_IME_COMPOSITION and (lp & GCS_RESULTSTR):
      result = _get_result_str(hwnd)
      if result:
          is_cjk = any(ord(c) > 127 for c in result)
          if is_cjk:
              # 中文字元：放行給 Blender 原生處理，保證選字與 Enter 正常提交
              return user32.CallWindowProcW(S.orig_proc, hwnd, msg, wp, lp)
          else:
              # ASCII (英數/符號)：攔截並透過 PostMessageW(WM_CHAR) 單次重注入
              for ch in result:
                  user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 0)
              # 若有其他非結果旗標則傳遞剩餘部分，否則 return 0
              remaining_flags = lp & ~GCS_RESULTSTR
              if remaining_flags:
                  return user32.CallWindowProcW(S.orig_proc, hwnd, msg, wp, remaining_flags)
              return 0
  ```

---

## 4. 測試驗證矩陣

| 測試項目 | 預期行為 | v11 狀態 | v12 修復設計 |
| :--- | :--- | :---: | :---: |
| **純數字 (1, 2, 3)** | 輸出單一數字 | ✅ 正常 | ✅ 正常 |
| **一般英文 (a-z, A-Z)** | 輸出單一字母 | ✅ 正常 | ✅ 正常 |
| **Shift + 符號 (!, $)** | 正常輸入 | ✅ 正常 | ✅ 正常 |
| **Shift + 符號 (@, #, %)** | 不重複出現 | ⚠️ 重複 | ✅ 納入 ASCII 攔截修復 |
| **中文輸入 + 選字 Enter** | 字串正常保留不消失 | ❌ 按 Enter 消失 | ✅ CJK 分流直接放行 |
| **英文輸入法模式** | 原生輸入正常 | ✅ 正常 | ✅ 正常 |

---

## 5. 下一步行動指南 (Next Steps)

1. **請使用者在 Blender 中執行 v12 驗證**：
   - 驗證中文字打完按 <kbd>Enter</kbd> 是否正常保留。
   - 驗證 `@`、`#`、`%` 等符號是否不再重複。
2. **包裝為正式 Blender Add-on / Extension**：
   - 將 PoC 整理為標準的 Blender Addon（包含 `bl_info` 或 `blender_manifest.toml`）。
   - 在 `register()` 與 `unregister()` 確保資源釋放與 WndProc 安全還原。
   - 支援多視窗（Multi-window）或視窗焦點切換自動重新 Hook。

---

## 6. Suggested Skills for Next Agent

* **`codebase-design`**：用於將 PoC 整理為具備高內聚、低耦合架構的正式 Blender 插件模組。
* **`tdd`**：若要編寫單元測試或模擬 Win32 訊息測試。
* **`writing-for-agents`**：後續維護技術文檔與 README。
