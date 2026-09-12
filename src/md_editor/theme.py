"""Window-local Qt palettes with an independently themed WebEngine document."""

from PySide6.QtGui import QColor, QPalette


def palette(dark: bool) -> QPalette:
    result = QPalette()
    colors = {
        "Window": ("#f5f7fa", "#20252d"),
        "WindowText": ("#253047", "#e1e7ef"),
        "Base": ("#ffffff", "#171c24"),
        "AlternateBase": ("#edf1f6", "#252d39"),
        "Text": ("#243244", "#e1e7ef"),
        "Button": ("#edf1f6", "#303947"),
        "ButtonText": ("#253047", "#e1e7ef"),
        "Highlight": ("#c6dcff", "#345480"),
        "HighlightedText": ("#162b49", "#ffffff"),
        "ToolTipBase": ("#ffffff", "#303947"),
        "ToolTipText": ("#253047", "#f0f4fa"),
        "PlaceholderText": ("#65758c", "#93a0b3"),
        "Link": ("#245ccd", "#88b9ff"),
    }
    for role, values in colors.items():
        result.setColor(getattr(QPalette.ColorRole, role), QColor(values[int(dark)]))
    result.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor("#7e8998" if dark else "#9299a3"),
    )
    result.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor("#7e8998" if dark else "#9299a3"),
    )
    return result


def scrollbar_stylesheet(dark: bool) -> str:
    """Match the preview.css scrollbars: 12px track, inset rounded 6px thumb."""
    track, thumb, hover, pressed = (
        ("#20252d", "#58677b", "#73849c", "#8b9bb0")
        if dark
        else ("#f5f7fa", "#a6b2c3", "#8796ac", "#65758c")
    )
    return f"""
        QScrollBar:vertical {{ background: {track}; width: 12px; margin: 0; border: none; }}
        QScrollBar:horizontal {{ background: {track}; height: 12px; margin: 0; border: none; }}
        QScrollBar::handle {{ background: {thumb}; border: 3px solid {track}; border-radius: 6px; }}
        QScrollBar::handle:vertical {{ min-height: 26px; }}
        QScrollBar::handle:horizontal {{ min-width: 26px; }}
        QScrollBar::handle:hover {{ background: {hover}; }}
        QScrollBar::handle:pressed {{ background: {pressed}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
    """
