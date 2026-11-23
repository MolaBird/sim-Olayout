"""Application entry point: ``python -m olayout [layout.olayout.json]``."""

from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from olayout import paths

log = logging.getLogger("olayout")


def _setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(RotatingFileHandler(paths.log_path(), maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


class _ExceptionHook:
    """Log unexpected errors and tell the user, but keep the app running."""

    def __init__(self) -> None:
        self._showing = False

    def __call__(self, exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            QApplication.quit()
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("Unhandled exception:\n%s", text)
        if self._showing or QApplication.instance() is None:
            return
        self._showing = True
        try:
            box = QMessageBox(QMessageBox.Icon.Warning, "Something went wrong", f"{exc_type.__name__}: {exc}")
            box.setInformativeText(
                "The application is still running and your layout is autosaved. "
                f"Details were written to {paths.log_path()}."
            )
            box.setDetailedText(text)
            box.exec()
        finally:
            self._showing = False


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    _setup_logging()
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName("Optical Layout")
    app.setOrganizationName("olayout")

    from olayout.ui.main_window import MainWindow
    from olayout.ui.settings import app_settings
    from olayout.ui.theme import apply_theme
    from olayout.ui.warmup import warm_sprite_cache

    apply_theme(app)
    sys.excepthook = _ExceptionHook()
    warm_sprite_cache()

    marker = paths.session_marker_path()
    crashed_last_time = marker.exists()
    try:
        marker.write_text("running", encoding="utf-8")
    except OSError:
        pass

    window = MainWindow()
    window.show()

    file_args = [a for a in argv[1:] if not a.startswith("-")]

    def _startup() -> None:
        if file_args:
            window.open_path(Path(file_args[0]), confirmed=True)
            return
        if crashed_last_time and window.offer_recovery():
            return
        settings = app_settings()
        demo = paths.EXAMPLES_DIR / "demo.olayout.json"
        if not settings.value("first_run_done", False) and demo.exists():
            window.open_path(demo, confirmed=True, as_template=True)
            settings.setValue("first_run_done", True)

    QTimer.singleShot(0, _startup)
    code = app.exec()
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
