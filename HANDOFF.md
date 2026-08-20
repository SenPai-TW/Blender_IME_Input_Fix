# Blender IME Input Fix handoff — 2026-08-20

## Next-session objective

Prepare the cleaned `v0.1.0` project for the user's own commit/push and first GitHub Draft Release. Do not commit, push, create a tag, or publish a Release unless the user explicitly asks.

## Repository state

- Repository: `D:\GitHub\Blender_IME_Input_Fix`
- Remote: `https://github.com/SenPai-TW/Blender_IME_Input_Fix.git`
- Branch: `main`
- HEAD and `origin/main`: `3e19b4a` (`V24 TEST HOOK修正中`)
- Working tree contains the complete Add-on packaging work and is intentionally uncommitted.
- No tag or GitHub Release has been created.

Run `git status --short --branch` for the exact current list. The intentional tracked deletions are:

- `conversation_history.md`
- `conversation_transcript_full.jsonl`
- `handoff_blender_ime_fix.md`
- `handoffs/2026-08-16-blender-ime-fix-v20.md`
- `ime_fix_poc.py`

The old conversation/handoff material remains recoverable from Git history. The formal package replaces the PoC launcher.

## Current implementation

Reference the files rather than duplicating their contents:

- Add-on metadata and lifecycle facade: `D:\GitHub\Blender_IME_Input_Fix\blender_ime_fix\__init__.py`
- Proven v24 Win32/IME runtime: `D:\GitHub\Blender_IME_Input_Fix\blender_ime_fix\core.py`
- Install/build/compatibility documentation: `D:\GitHub\Blender_IME_Input_Fix\README.md`
- Deterministic ZIP builder: `D:\GitHub\Blender_IME_Input_Fix\tools\build_addon.py`
- Regression tests: `D:\GitHub\Blender_IME_Input_Fix\tests\`
- Repository license: `D:\GitHub\Blender_IME_Input_Fix\LICENSE`
- Ignore rules: `D:\GitHub\Blender_IME_Input_Fix\.gitignore`

Important decisions and constraints:

- Package version is `0.1.0`; intended Release tag is `v0.1.0`.
- License is `GPL-3.0-or-later`; both shipped Python files have SPDX headers, and the install ZIP includes `blender_ime_fix/LICENSE`.
- Remain pure Python + `ctypes`; only public Windows DLL/API calls are allowed.
- Do not add a custom native DLL, Blender private-memory offsets, or per-Blender-version addresses.
- Legacy Add-on ZIP is intentional so the declared compatibility floor can remain Blender 2.80. Blender 2.80 is not installed locally and must not be described as verified.
- The diagnostics N-panel code remains in `core.py`, but is hidden by default. The Add-on Preferences checkbox `顯示開發者診斷面板` registers/removes it dynamically without stopping the IME fix.
- Shifted number-row symbols use the queued `GetKeyState(VK_SHIFT)` state. When Shift is down, Bopomofo first-key Raw Input suppression fails open so `@#%&*` can reach the existing character-deduplication path.
- `dist/` is ignored by Git. The install ZIP should be uploaded as a GitHub Release Asset, not committed. GitHub will create its own source-code archives from the tag.

## Release artifact

- Local artifact: `D:\GitHub\Blender_IME_Input_Fix\dist\blender_ime_input_fix-v0.1.0.zip`
- SHA-256: `7FDEA303F9DC876C3B6157A060F76511890A30282668C232057526152CB81B79`
- ZIP members:
  - `blender_ime_fix/__init__.py`
  - `blender_ime_fix/core.py`
  - `blender_ime_fix/LICENSE`

Rebuild with:

```powershell
python tools\build_addon.py
```

## Verification evidence

- Latest offline suite: 27/27 passed.
- Run command: `python -m unittest discover -s tests -v`
- `git diff --check` passed; the only messages are the repository's existing Git LF/CRLF conversion warnings.
- Earlier full ZIP lifecycle matrix passed on installed Blender versions 3.0.0, 3.3.8, 3.4.1, 3.6.0, 4.0.2, 4.2.0, 4.2.7, 4.5.6, 4.5.8, 5.0.1, 5.1.1, and 5.2.0.
- After the Shift-symbol fix, the final ZIP was re-tested successfully on Blender 3.0.0 and 5.2.0.
- Actual input behavior was previously confirmed by the user in Blender 5.2 on Windows 11 with Microsoft Bopomofo: duplicate ASCII/numpad cases and the initial Bopomofo raw-input issue were fixed.

## Recommended next steps

1. Ask the user to install the exact final ZIP once more in Blender 5.2 and confirm `@#%&*`, unmodified Bopomofo first keys, normal typing, and the Add-on Preferences diagnostics toggle.
2. Run `code-review` against `HEAD` before committing; review both tracked deletions and all untracked source files.
3. If the review is clean and the user explicitly authorizes it, commit and push the source changes. Keep `dist/` uncommitted.
4. Create a GitHub Draft Release targeting the pushed commit with tag `v0.1.0`, title `Blender IME Input Fix v0.1.0`, and upload the ignored ZIP as the installable asset.
5. Keep the Release as a draft until the uploaded asset is downloaded and installed successfully; then publish as Latest only with explicit user approval.
6. GitHub Actions release automation is deferred until the manual Release workflow is proven.

## Suggested skills

- `code-review`: inspect the entire uncommitted change set against `HEAD` before the first public release.
- `github:github`: orient the repository and Draft Release state when the user is ready to work on GitHub.
- `github:yeet`: use only if the user explicitly asks the agent to commit/push or open a PR; it is not authorization to publish a Release.
- `diagnosing-bugs`: use if final installed-ZIP testing exposes a regression.

## Safety and workflow notes

- The user has explicitly said not to commit yet.
- Preserve the current working tree; do not discard or reset unrelated changes.
- At the start of a fresh tool-using session, follow the active `AGENTS.md` policy-loading and integrity-check instructions before filesystem, terminal, connector, or GitHub operations.
