# Blender IME Input Fix

Windows 專用 Blender Add-on，用來修正 Microsoft 注音輸入法造成的重複字元，以及第一個注音按鍵殘留數字的問題。

## 安裝

1. 從 GitHub Releases 下載 `blender_ime_input_fix-v0.1.0.zip`；開發者也可執行 `python tools/build_addon.py` 自行建置。
2. 在 Blender 開啟 `Edit > Preferences > Add-ons`。
3. 選擇 `Install from Disk`，選取下載的 `blender_ime_input_fix-v0.1.0.zip`。
4. 啟用 `System: Blender IME Input Fix`。

Add-on 啟用後會自動啟動修復，不會在 3D Viewport 顯示額外的 N 面板。停用 Add-on 時會移除所有已安裝的 Windows subclass hook。

> GitHub 自動產生的 `Source code (zip)` 與 `Source code (tar.gz)` 是完整專案原始碼，不是 Blender 安裝包。

## 開發面板

診斷面板的程式碼仍保留，但預設不顯示。需要查看 Hook 狀態與事件日誌時：

1. 開啟 `Edit > Preferences > Add-ons`。
2. 展開 `System: Blender IME Input Fix` 的設定。
3. 勾選 `顯示開發者診斷面板`。

切換後會立即在 3D Viewport 的 N 面板加入或移除 `IME 診斷` 分頁，不需要重新安裝 Add-on。

## 相容性策略

- 使用 Blender 公開的 Python Add-on 註冊介面。
- 使用 Windows 公開的 `SetWindowSubclass`、`RemoveWindowSubclass` 與 IME API。
- 不讀取 Blender 私有記憶體，不使用版本位址或自製 DLL。
- `bl_info` 宣告的最低目標版本是 Blender 2.80；沒有實機版本就不宣稱已驗證。

目前封裝為傳統 Add-on ZIP，因為它能涵蓋 Blender 2.8，且 Blender 4.2 以後仍可從磁碟安裝。未來可在相同核心外加入 `blender_manifest.toml`，另外提供 Blender Extension 封裝。

## 已知範圍

- 作業系統：Windows。
- 目前實測輸入法：Windows 11 Microsoft 注音。
- 字元攔截名單來自實際重複問題，不會全面攔截英文或所有符號。

## 授權

本專案採用 [GNU General Public License v3.0 or later](LICENSE)，SPDX 識別碼為 `GPL-3.0-or-later`。安裝 ZIP 內亦包含完整授權文字。

## 驗證狀態

- 25 項離線回歸測試通過。
- 安裝 ZIP 的啟用／停用生命週期已在 Blender 3.0.0、3.3.8、3.4.1、3.6.0、4.0.2、4.2.0、4.2.7、4.5.6、4.5.8、5.0.1、5.1.1、5.2.0 通過。
- 實際輸入修復已由 Windows 11、Microsoft 注音、Blender 5.2 的 GUI 測試確認。
- Blender 2.80 是相容目標，但目前沒有本機安裝可驗證，因此不列為已驗證版本。
