"""Window-local surfaces and controls for the notebook application."""

from .theme import scrollbar_stylesheet


def notebook_stylesheet(dark: bool) -> str:
    chrome = "#20252d" if dark else "#f5f7fa"
    surface = "#171c24" if dark else "#ffffff"
    foreground = "#e1e7ef" if dark else "#253047"
    muted = "#93a0b3" if dark else "#65758c"
    border = "#353e4b" if dark else "#dce2ea"
    hover = "#303947" if dark else "#e8edf4"
    selected = "#345480" if dark else "#dce9fc"
    accent = "#88b9ff" if dark else "#346bc6"
    return f"""
        QWidget#notebookWorkspace, QStackedWidget#notebookPages,
        QWidget#notebookEmpty {{ background: {surface}; color: {foreground}; }}
        QWidget#libraryMatchBar, QWidget#noteSearchBar {{
            background: {chrome}; color: {foreground};
            border: none; border-bottom: 1px solid {border};
        }}
        QLabel {{ color: {foreground}; background: transparent; }}
        QLabel#notebookEmptyTitle {{ font-size: 20pt; font-weight: 600; }}
        QLabel#notebookEmptyHint {{ color: {muted}; margin-bottom: 16px; }}
        QStatusBar {{
            background: {chrome}; color: {muted};
            border: none; border-top: 1px solid {border};
        }}
        QStatusBar::item {{ border: none; }}
        QStatusBar QLabel {{ color: {muted}; padding: 2px 6px; }}
        QSplitter#noteSplitter::handle:horizontal {{
            background: {border}; width: 1px;
        }}
        QSplitter#noteSplitter::handle:horizontal:hover {{ background: {accent}; }}
        QPushButton {{
            background: {surface}; color: {foreground};
            border: 1px solid {border}; border-radius: 5px; padding: 5px 12px;
            min-height: 18px;
        }}
        QPushButton:hover {{ background: {hover}; }}
        QPushButton:pressed, QPushButton:checked {{ background: {selected}; }}
        QPushButton:focus {{ border-color: {accent}; }}
        QPushButton:disabled {{ color: {muted}; background: {chrome}; }}
        QToolButton#notebookSidebarToggle {{
            background: transparent; color: {foreground}; border: none;
            border-radius: 5px; padding: 0;
        }}
        QToolButton#notebookSidebarToggle:hover {{ background: {hover}; }}
        QToolButton#notebookSidebarToggle:checked {{ background: {selected}; }}
        QToolButton#notebookSidebarToggle:focus {{ border: 1px solid {accent}; }}
        QLineEdit {{
            background: {surface}; color: {foreground};
            border: 1px solid {border}; border-radius: 5px; padding: 5px 8px;
            selection-background-color: {selected}; selection-color: {foreground};
        }}
        QLineEdit:focus {{ border-color: {accent}; }}
        QToolTip {{
            background: {chrome}; color: {foreground};
            border: 1px solid {border}; padding: 5px 8px;
        }}
    """ + scrollbar_stylesheet(dark)
