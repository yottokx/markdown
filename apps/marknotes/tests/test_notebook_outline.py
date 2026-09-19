"""Markdown outline parsing and accessible navigation, without a web view."""

from PySide6.QtCore import Qt

from marknotes.notebook_outline import Heading, NotebookOutlinePanel, markdown_headings


def test_outline_uses_markdown_structure_and_visible_inline_text():
    source = """# 見出し **太字** [リンク](https://example.test) `code` &amp; 😀
本文
### 子 ![画像](assets/a.png) <em>斜体</em>
次の見出し
----------

> ## 引用

- ### リスト

```markdown
# コード内
```

    # インデントコード

$$
# 数式内
$$

<div>
# HTMLブロック内
</div>
"""
    assert markdown_headings(source) == (
        Heading(1, "見出し 太字 リンク code & 😀", 0),
        Heading(3, "子 画像 斜体", 2),
        Heading(2, "次の見出し", 3),
        Heading(2, "引用", 6),
        Heading(3, "リスト", 8),
    )


def test_outline_handles_empty_and_duplicate_headings():
    assert markdown_headings("#\n\n## 同じ\n\n## 同じ\n") == (
        Heading(1, "（空の見出し）", 0),
        Heading(2, "同じ", 2),
        Heading(2, "同じ", 4),
    )
    assert markdown_headings("本文\n\n---\n") == ()


def test_outline_tree_hierarchy_navigation_and_note_switch(qtbot):
    panel = NotebookOutlinePanel()
    qtbot.addWidget(panel)
    panel.set_source("first", "# 親\n### 子\n## 次の子\n# 次の親\n")
    assert panel.tree.topLevelItemCount() == 2
    parent = panel.tree.topLevelItem(0)
    assert parent.childCount() == 2
    child = parent.child(1)
    assert child.text(0) == "次の子"
    assert "H2" in child.toolTip(0)
    with qtbot.waitSignal(panel.heading_requested) as signal:
        panel.tree.itemActivated.emit(child, 0)
    assert signal.args == ["first", 2]
    panel.set_source("second", "## 別ノート")
    assert panel.tree.topLevelItemCount() == 1
    assert panel.tree.topLevelItem(0).text(0) == "別ノート"
    panel.set_source(None, "")
    assert panel.tree.topLevelItemCount() == 0
    assert "ノートを開く" in panel.status_label.text()


def test_outline_preserves_collapsed_state_and_selection_on_edits(qtbot):
    panel = NotebookOutlinePanel()
    qtbot.addWidget(panel)
    panel.set_source("note", "# 親\n## 子\n")
    panel.tree.topLevelItem(0).setExpanded(False)
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    panel.set_source("note", "本文\n\n# 親\n## 子\n")
    assert not panel.tree.topLevelItem(0).isExpanded()
    assert panel.tree.currentItem().text(0) == "親"
    assert panel.tree.topLevelItem(0).data(0, Qt.ItemDataRole.UserRole) == 2
    panel.set_source("note", "本文を変更\n\n# 親\n## 子\n")
    assert not panel.tree.topLevelItem(0).isExpanded()


def test_outline_local_link_labels_match_the_preview():
    assert markdown_headings(
        "# [資料](file:///C:/docs/report.pdf) ![画像](file:///C:/docs/image.png)"
    ) == (Heading(1, "資料 画像", 0),)
