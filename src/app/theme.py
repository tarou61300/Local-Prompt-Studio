from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory, QToolTip, QWidget

from core.config_manager import THEME_DARK, THEME_NORMAL, normalize_theme


_ORIGINAL_PALETTE_ATTRIBUTE = "_local_prompt_studio_normal_palette"
_ORIGINAL_TOOLTIP_PALETTE_ATTRIBUTE = "_local_prompt_studio_normal_tooltip_palette"
_ORIGINAL_STYLE_NAME_ATTRIBUTE = "_local_prompt_studio_normal_style_name"
_THEME_PROPERTY = "local_prompt_studio_theme"
_NORMAL_ERROR_COLOR = QColor("#b00020")
_DARK_ERROR_COLOR = QColor("#ff7b72")
_DARK_PROMPT_EDITOR_STYLESHEET = """
QPlainTextEdit {
    border: 1px solid #4a4f55;
}
QPlainTextEdit:focus {
    border: 1px solid #6d8fb3;
}
""".strip()


def error_text_stylesheet(palette: QPalette) -> str:
    """Return a readable semantic error color without mutating the app palette."""
    background = palette.color(QPalette.ColorRole.Window)
    color = (
        _DARK_ERROR_COLOR
        if background.lightness() < 128
        else _NORMAL_ERROR_COLOR
    )
    return f"color: {color.name()};"


def apply_prompt_editor_theme(widget: QWidget, theme: object) -> None:
    """Apply the dark outline only to a designated prompt editor."""
    stylesheet = (
        _DARK_PROMPT_EDITOR_STYLESHEET
        if normalize_theme(theme) == THEME_DARK
        else ""
    )
    widget.setStyleSheet(stylesheet)


def build_dark_palette() -> QPalette:
    """Return the application-owned dark palette."""
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#202124",
        QPalette.ColorRole.WindowText: "#e8eaed",
        QPalette.ColorRole.Base: "#17181a",
        QPalette.ColorRole.AlternateBase: "#25272a",
        QPalette.ColorRole.ToolTipBase: "#303134",
        QPalette.ColorRole.ToolTipText: "#f1f3f4",
        QPalette.ColorRole.Text: "#e8eaed",
        QPalette.ColorRole.Button: "#2b2d30",
        QPalette.ColorRole.ButtonText: "#e8eaed",
        QPalette.ColorRole.BrightText: "#ff7b72",
        QPalette.ColorRole.Link: "#8ab4f8",
        QPalette.ColorRole.LinkVisited: "#c58af9",
        QPalette.ColorRole.Highlight: "#3f72af",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Light: "#3c4043",
        QPalette.ColorRole.Midlight: "#34373a",
        QPalette.ColorRole.Dark: "#151618",
        QPalette.ColorRole.Mid: "#767b81",
        QPalette.ColorRole.Shadow: "#0d0e0f",
        QPalette.ColorRole.PlaceholderText: "#b3b7bd",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))

    disabled = QPalette.ColorGroup.Disabled
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(disabled, role, QColor("#aeb3b8"))
    palette.setColor(disabled, QPalette.ColorRole.Highlight, QColor("#374151"))
    palette.setColor(disabled, QPalette.ColorRole.HighlightedText, QColor("#c2c6ca"))
    palette.setColor(disabled, QPalette.ColorRole.PlaceholderText, QColor("#8f949a"))
    return palette


def _repolish_application_widgets(app: QApplication) -> None:
    """Rebind widget-local stylesheets and palettes to the current app style."""
    for top_level in app.topLevelWidgets():
        if not top_level.isVisible():
            continue
        widgets = (
            top_level,
            *(
                widget
                for widget in top_level.findChildren(QWidget)
                if widget.window() is top_level
            ),
        )
        local_stylesheets = [
            (widget, widget.styleSheet())
            for widget in widgets
            if widget.styleSheet()
        ]
        for widget, _stylesheet in local_stylesheets:
            widget.setStyleSheet("")
        for widget, stylesheet in local_stylesheets:
            widget.setStyleSheet(stylesheet)
        for widget in widgets:
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            refresh_theme = getattr(widget, "refresh_theme", None)
            if callable(refresh_theme):
                refresh_theme()
            widget.updateGeometry()
            widget.update()
        top_level.updateGeometry()
        top_level.update()


def apply_application_theme(app: QApplication, theme: object) -> str:
    """Apply a normalized theme immediately and return its stable config ID."""
    if not hasattr(app, _ORIGINAL_PALETTE_ATTRIBUTE):
        setattr(app, _ORIGINAL_PALETTE_ATTRIBUTE, QPalette(app.palette()))
        setattr(
            app,
            _ORIGINAL_TOOLTIP_PALETTE_ATTRIBUTE,
            QPalette(QToolTip.palette()),
        )
        setattr(app, _ORIGINAL_STYLE_NAME_ATTRIBUTE, app.style().objectName())

    normalized = normalize_theme(theme)
    if normalized == THEME_DARK:
        if app.style().objectName().lower() != "fusion":
            fusion_style = QStyleFactory.create("Fusion")
            if fusion_style is not None:
                app.setStyle(fusion_style)
        dark_palette = build_dark_palette()
        app.setPalette(dark_palette)
        QToolTip.setPalette(dark_palette)
    else:
        original_style_name = getattr(app, _ORIGINAL_STYLE_NAME_ATTRIBUTE)
        if app.style().objectName().lower() != original_style_name.lower():
            original_style = QStyleFactory.create(original_style_name)
            if original_style is not None:
                app.setStyle(original_style)
        original_palette = getattr(app, _ORIGINAL_PALETTE_ATTRIBUTE)
        normal_palette = QPalette(original_palette)
        app.setPalette(normal_palette)
        QToolTip.setPalette(
            QPalette(getattr(app, _ORIGINAL_TOOLTIP_PALETTE_ATTRIBUTE))
        )
        normalized = THEME_NORMAL

    app.setProperty(_THEME_PROPERTY, normalized)
    _repolish_application_widgets(app)
    return normalized


def current_application_theme(app: QApplication) -> str:
    return normalize_theme(app.property(_THEME_PROPERTY))
