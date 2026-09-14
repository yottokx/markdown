# MarkNotes development

- Change and test MarkNotes only unless the user explicitly requests work on the original editor.
- Automated checks must not show windows, popup menus, or dialogs on the user's desktop. Use `QT_QPA_PLATFORM=offscreen`; tests/conftest.py sets this default.
- Inspect context menus through their constructed actions and trigger actions directly. Mock modal menu/dialog execution; do not leave a real popup waiting for input.
- Do not switch to visible Windows tests to work around an offscreen test failure. Diagnose the failure or report the limitation. Visible UI verification requires an explicit user request.
- Use isolated test libraries under temporary directories or artifacts. Do not test deletion against the user's library.
