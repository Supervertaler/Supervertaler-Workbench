# Release handoff – v1.10.372 (Windows build + GitHub release)

> For Claude Code on Michael's Windows PC. Prepared in a Linux cloud session, which
> could not build a Windows EXE or create GitHub releases. Delete this file in the
> last step.

## Already done

- PR #251 (22 issues) was merged into `main`.
- `pyproject.toml` was bumped to **1.10.372**. This is the only version source; `Supervertaler.py` reads it at runtime.
- `CHANGELOG.md`: the "Unreleased" entries are now **v1.10.372 – September 29, 2026**, and the "Current Version" line is updated.
- `release-notes-v1.10.372.md` (repo root) is the finished GitHub release body.
- Nothing build-related changed since v1.10.371:
  - no changes to `okapi-sidecar/`, `Supervertaler.spec`, `build_windows_release.ps1`, `create_release_zip.py` or the requirements;
  - new code is plain Python in `modules/`, which the spec ships as a whole folder.

## Steps

1. **Update the checkout.**
   ```powershell
   git checkout main
   git pull
   Select-String '^version' pyproject.toml   # must say 1.10.372
   ```

2. **Okapi sidecar.** The build bundles `okapi-sidecar\dist\okapi-sidecar.jar` and `okapi-sidecar\dist\jre\`. These are git-ignored and should still be there from the v1.10.371 build, so reuse them. Only if either is missing, run `okapi-sidecar\build.ps1 -JLink` (needs a JDK 17+ and Maven).

3. **Run the tests.** Optional, about 3 seconds:
   ```powershell
   .\.venv-build\Scripts\python.exe -m pytest tests -q   # or any venv with the requirements
   ```
   Expect 264 passed.

4. **Build.**
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows_release.ps1
   ```
   The output is `dist\Supervertaler-v1.10.372-Windows.zip`, about 480–500 MB. v1.10.371 was 498 MB.

5. **Smoke-test the built EXE before publishing.** None of this release has been run on Windows yet. Extract the ZIP to a temp folder, run `Supervertaler\Supervertaler.exe`, and check the items below. If anything fails, **stop and don't publish**: fix it on `main` and rebuild, or report back to Michael.
   - [ ] **Version.** The app starts, and the title or About shows **1.10.372**.
   - [ ] **AltGr (Windows-only fix, #243).** With Supervertaler running, switch to a Polish (Programmer) or other AltGr keyboard layout. In Notepad, AltGr+A must type `ą` (or that layout's character). One Supervertaler global hotkey must still work.
   - [ ] **Settings auto-save (#214).**
     - The General, AI, View, System Prompts and other pages have no 💾 Save button.
     - Tick a checkbox on General, restart the app, and it must still be ticked.
     - No "Settings saved" pop-ups appear.
   - [ ] **Settings → 📏 Segmentation Rules (#191)** opens, and its Test box updates when you add a rule with "➕ Break after text…". **📥 Import SRX** opens a file dialog.
   - [ ] **Settings → 🏷️ Inline Codes (#194)** opens, and "➕ Common patterns" → `{placeholder}` marks `{PKMN}` in the Test box.
   - [ ] **Dark theme (#78).** Switch to Dark and look at the Clipboard tab and SuperLookup: there must be no white panels.
   - [ ] **Help → 🎓 Open Sample Project** opens, with a 100% match and fuzzy matches.
   - [ ] **The usual round-trip.** Import a DOCX (the Okapi sidecar starts), translate a segment, then export.

6. **Create the release.** This creates the `v1.10.372` tag on `main` and uploads the ZIP:
   ```powershell
   gh release create v1.10.372 --target main `
     --title "v1.10.372 – your own segmentation rules, protected placeholders, no more Save buttons" `
     --notes-file release-notes-v1.10.372.md `
     "dist\Supervertaler-v1.10.372-Windows.zip"
   ```
   Then check the release page: the ZIP asset must be listed, and the tag must point at `main`'s head.

7. **PyPI (optional).** The notes say `pip install --upgrade supervertaler`, but PyPI stops at **1.10.351**; releases 1.10.367–371 were never uploaded. Do one of these:
   - Publish it. Use `dist_pypi`, not `dist`, because `dist\` holds the Windows build:
     ```powershell
     python -m build --outdir dist_pypi
     twine upload dist_pypi\supervertaler-1.10.372*
     ```
   - Or drop the pip line from the notes and run `gh release edit v1.10.372 --notes-file release-notes-v1.10.372.md`.

8. **Clean up.** Delete this file, commit ("Remove v1.10.372 release handoff") and push to `main`.

## If something breaks in the smoke test

The risky areas, most to least likely to surprise on Windows:
- the AltGr hotkey handling, in `modules/platform_helpers.py` (`is_altgr_chord`, `altgr_character`, the hotkey message loop);
- Settings auto-save, in `modules/settings_autosave.py`: it clicks each page's hidden Save button after user edits;
- the dark-theme stylesheet adapter, in `modules/dark_style_adapter.py`.

Each one is covered in CHANGELOG.md under v1.10.372, in `tests/`, and in the PR #251 description.
