"""Full output workflow using real WebEngine, embedded resources and PDF bytes."""

import json
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage, QPageLayout
from PySide6.QtWebEngineCore import QWebEngineScript
from PySide6.QtWidgets import QMessageBox

import md_editor.export_dialog as module
from md_editor.export_dialog import ExportDialog


def evaluate(qtbot, dialog, expression):
    results = []
    dialog.page.runJavaScript(
        "JSON.stringify(" + expression + ")",
        QWebEngineScript.ScriptWorldId.ApplicationWorld,
        results.append,
    )
    qtbot.waitUntil(lambda: bool(results), timeout=10000)
    return json.loads(results[0])


def open_dialog(qtbot, tmp_path, source, **kwargs):
    settings = QSettings(str(tmp_path / "export.ini"), QSettings.Format.IniFormat)
    dialog = ExportDialog(source, tmp_path, settings=settings, **kwargs)
    qtbot.addWidget(dialog)
    with qtbot.waitSignal(dialog.ready, timeout=30000):
        dialog.show()
    return dialog


def test_preview_matches_saved_html_with_images_math_mermaid_and_custom_css(qtbot, tmp_path):
    image = QImage(30, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("#e8823a"))
    assert image.save(str(tmp_path / "image.png"))
    source = (
        "# 出力\n\n本文 $ x_i $\n\n![画像](image.png)\n\n```mermaid\nflowchart LR\nA --> B\n```"
    )
    dialog = open_dialog(qtbot, tmp_path, source)
    css = tmp_path / "custom.css"
    css.write_text("h1 { color: rgb(17, 85, 153); }", encoding="utf-8")
    dialog.css_path.setText(str(css))
    dialog.custom_css.setChecked(True)
    assert not dialog.html_button.isEnabled()
    with qtbot.waitSignal(dialog.ready, timeout=30000):
        dialog.refresh_button.click()
    assert (
        evaluate(qtbot, dialog, "getComputedStyle(document.querySelector('h1')).color")
        == "rgb(17, 85, 153)"
    )
    assert evaluate(qtbot, dialog, "document.querySelectorAll('.katex').length") == 1
    assert evaluate(qtbot, dialog, "document.querySelectorAll('.mermaid-block svg').length") == 1
    assert evaluate(qtbot, dialog, "document.images[0].naturalWidth") == 30
    assert evaluate(qtbot, dialog, "document.scripts.length") == 0
    assert "data-source-line" not in dialog.html and 'id="eof-spacer"' not in dialog.html
    target = tmp_path / "別フォルダ" / "日本語.html"
    target.parent.mkdir()
    assert dialog.save_html(target)
    assert target.read_text(encoding="utf-8") == dialog._preview_path.read_text(encoding="utf-8")
    assert dialog.source == source
    (tmp_path / "image.png").unlink()
    with qtbot.waitSignal(dialog.page.loadFinished, timeout=10000):
        dialog.page.load_html_file(target)
    assert evaluate(qtbot, dialog, "document.images[0].naturalWidth") == 30


def test_picture_candidates_survive_markdown_render_and_are_embedded(qtbot, tmp_path):
    for name, width in (("fallback", 20), ("candidate", 60)):
        image = QImage(width, 20, QImage.Format.Format_RGB32)
        image.fill(QColor("#4499bb"))
        assert image.save(str(tmp_path / (name + ".png")))
    source = (
        '<picture><source srcset="candidate.png 1x"><img src="fallback.png" alt="候補"></picture>'
    )
    dialog = open_dialog(qtbot, tmp_path, source)
    assert evaluate(qtbot, dialog, "document.images[0].naturalWidth") == 60
    assert evaluate(
        qtbot,
        dialog,
        "document.querySelector('source').srcset.startsWith('data:image/png;base64,')",
    )
    assert evaluate(qtbot, dialog, "document.images[0].src.startsWith('data:image/png;base64,')")


def test_missing_image_is_reported_and_can_be_fixed_without_closing(qtbot, tmp_path):
    dialog = ExportDialog("![missing](absent.png)", tmp_path)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.errors.isVisible(), timeout=30000)
    assert "absent.png" in dialog.errors.toPlainText()
    assert not dialog.html_button.isEnabled()
    image = QImage(20, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("#4499bb"))
    assert image.save(str(tmp_path / "absent.png"))
    with qtbot.waitSignal(dialog.ready, timeout=30000):
        dialog.refresh()
    assert dialog.html_button.isEnabled() and not dialog.errors.isVisible()


def test_pdf_saves_only_after_completion_and_keeps_source_unchanged(qtbot, tmp_path):
    source_path = tmp_path / "original.md"
    source_path.write_text("# Original", encoding="utf-8")
    source = "# Edited\n\n$ x^2 $\n\n| A | B |\n| --- | --- |\n| 1 | 2 |"
    dialog = open_dialog(qtbot, tmp_path, source, source_path=source_path)
    dialog.paper.setCurrentText("A5")
    dialog.orientation.setCurrentIndex(1)
    assert dialog.page_layout().orientation() == QPageLayout.Orientation.Landscape
    target = tmp_path / "日本語.pdf"
    with qtbot.waitSignal(dialog.saved, timeout=30000):
        assert dialog.save_pdf(target)
        assert not target.exists()
    assert target.read_bytes().startswith(b"%PDF-")
    assert source_path.read_text(encoding="utf-8") == "# Original"
    assert dialog.source == source and dialog.pdf_button.isEnabled()


def test_failed_save_preserves_existing_output_and_does_not_overwrite_markdown(
    qtbot, tmp_path, monkeypatch
):
    source_path = tmp_path / "original.md"
    source_path.write_text("# Original", encoding="utf-8")
    dialog = open_dialog(qtbot, tmp_path, "# Edited", source_path=source_path)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    assert not dialog.save_html(source_path)
    assert source_path.read_text(encoding="utf-8") == "# Original"
    target = tmp_path / "existing.html"
    target.write_text("keep me", encoding="utf-8")

    def fail_write(*args):
        raise OSError("write failed")

    monkeypatch.setattr(module, "atomic_write", fail_write)
    assert not dialog.save_html(target)
    assert target.read_text(encoding="utf-8") == "keep me"
    assert len(warnings) == 2


def test_close_during_pdf_prevents_late_callback_from_writing(qtbot, tmp_path, monkeypatch):
    dialog = open_dialog(qtbot, tmp_path, "# Report")
    monkeypatch.setattr(dialog.pdf_renderer, "render", lambda *args: None)
    target = tmp_path / "cancelled.pdf"
    assert dialog.save_pdf(target)
    dialog.reject()
    dialog._pdf_ready(b"%PDF-fake")
    assert not target.exists()
    assert not Path(dialog._temporary.name).exists()


def test_invalid_pdf_margins_do_not_create_a_file(qtbot, tmp_path, monkeypatch):
    dialog = open_dialog(qtbot, tmp_path, "# Report")
    dialog.paper.setCurrentText("A5")
    dialog.margins["left"].setValue(80)
    dialog.margins["right"].setValue(80)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    assert not dialog.save_pdf(tmp_path / "invalid.pdf")
    assert warnings and not (tmp_path / "invalid.pdf").exists()


def test_old_page_completion_does_not_fail_a_new_preview(qtbot, tmp_path):
    dialog = open_dialog(qtbot, tmp_path, "# Report")
    previous, revision = dialog.page, dialog._revision
    with qtbot.waitSignal(dialog.ready, timeout=30000):
        dialog.refresh()
        dialog.refresh()
    assert dialog.page is not previous
    dialog._loaded(revision, previous, False)
    dialog._loaded(revision, previous, True)
    assert dialog.html_button.isEnabled()
    assert not dialog.errors.isVisible()
    assert evaluate(qtbot, dialog, "document.querySelector('h1').textContent") == "Report"
