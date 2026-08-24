# Blender IME Input Fix handoff — 2026-08-24

## Next-session objective

Commit the `0.1.1` version bump, then create the matching tag and GitHub Release when ready. Do not commit, tag, push, or publish a release unless the user explicitly requests it.

## Repository state

- Repository: `D:\GitHub\Blender_IME_Input_Fix`
- Remote: `https://github.com/SenPai-TW/Blender_IME_Input_Fix.git`
- Branch: `main`
- Current HEAD and `origin/main`: `994da44` (`fix: harden composition buffer calculation and log exception handling`)
- Existing Git tag: `v0.1.0` points to `a3d3906`; `v0.1.1` has not been created.
- Working tree: Contains the intentional `0.1.1` version bump in metadata, README, tests, and this handoff.

## Current implementation & recent changes

- Add-on metadata and lifecycle facade: `blender_ime_fix/__init__.py`
- Core Win32/IME runtime: `blender_ime_fix/core.py`
  - **Security hardening committed in `994da44` (2026-08-23)**:
    - **M1 Fix**: Corrected buffer size passed to `ImmGetCompositionStringW` (`buf_chars * 2`) to strictly match the allocated buffer and prevent theoretical off-by-one under non-standard byte counts.
    - **L1 Fix**: Replaced bare `except:` with explicit `except Exception:` in `_handle_message` logging branches.
- Install/build/compatibility documentation: `README.md`
- Deterministic ZIP builder: `tools/build_addon.py`
- Regression tests: `tests/` (27/27 passing)
- License: `LICENSE` (GPL-3.0-or-later)
- Git Ignore: `.gitignore`

## Release artifact

- Local artifact: `dist/blender_ime_input_fix-v0.1.1.zip`
- SHA-256: `ADFCC68245BE7AA7A55F368CC8519EAC32849D3B141AA586D9BD19C7A468CCA5`
- ZIP members:
  - `blender_ime_fix/__init__.py`
  - `blender_ime_fix/core.py`
  - `blender_ime_fix/LICENSE`

Rebuild with:

```powershell
python tools\build_addon.py
```

## Verification evidence

- Latest offline suite: **27/27 passed**.
- Run command: `python -m unittest discover -s tests -v`
- The rebuilt `v0.1.1` ZIP passed lifecycle checks on Blender 3.0.0 and 5.2.0.
- An earlier `v0.1.0` ZIP passed the full installed-version lifecycle matrix from Blender 3.0.0 through 5.2.0.
- Actual input behavior confirmed in Blender 5.2 on Windows 11 with Microsoft Bopomofo (duplicate ASCII/numpad, symbols, and Bopomofo raw-input initial key suppression all resolved).

## Suggested next steps

1. Review and commit the `0.1.1` version bump using a release-preparation commit message such as:
   ```text
   chore: bump add-on version to 0.1.1
   ```
2. Push the commit to `origin/main`, then create tag `v0.1.1` on that exact commit.
3. Create the GitHub Release from tag `v0.1.1` and upload `dist/blender_ime_input_fix-v0.1.1.zip`.

## Suggested skills

- `code-review`: inspect changes before final release if needed.
- `github:github`: manage GitHub releases and assets.
