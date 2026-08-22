from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow
from app.setup_dialog import SetupDialog
from app.theme import apply_application_theme
from core.config_manager import ConfigManager, PORTABLE_WRITE_ERROR
from core.localization import Localization
from core.version import APP_VERSION, INTERNAL_APPLICATION_ID, PRODUCT_NAME


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Prompt Studio")
    parser.add_argument("--mock", action="store_true", help="Use the bundled development mock server")
    parser.add_argument("--skip-setup", action="store_true", help="Skip the first-run wizard for testing")
    parser.add_argument(
        "--portable-data",
        type=Path,
        help="Development/test data folder (ignored by packaged builds)",
    )
    parser.add_argument("--dev-skill-path", type=Path, help="Use a development Skill fixture")
    parser.add_argument("--smoke-test", action="store_true", help="Open the window briefly, then exit")
    return parser.parse_args()


def configure_logging(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        data_dir / "local-prompt-studio.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.info("Application starting (prompt contents are never logged)")


def application_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    """Return resource root and writable data root without using the registry."""
    if getattr(sys, "frozen", False):
        resource_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        portable_root = Path(sys.executable).resolve().parent
        # A packaged build is unconditionally portable. Command-line arguments
        # must never redirect application-owned persistent data outside it.
        data_dir = portable_root / "data"
    else:
        resource_root = Path(__file__).resolve().parents[1]
        data_dir = args.portable_data or resource_root / ".dev-data"
    return resource_root.resolve(), Path(data_dir).resolve()


def main() -> int:
    args = parse_args()
    project_root, data_dir = application_paths(args)
    config_manager = ConfigManager(data_dir)
    config = config_manager.load()
    localization = Localization(project_root / "locales", config.ui_locale)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(PRODUCT_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(INTERNAL_APPLICATION_ID)
    apply_application_theme(app, config.theme)
    try:
        config_manager.ensure_writable()
        configure_logging(config_manager.data_dir)
    except OSError:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(None, "保存場所エラー", PORTABLE_WRITE_ERROR)
        return 1

    mock_server = None
    server_url = None
    dev_skill_path = args.dev_skill_path
    if args.mock:
        from mock_server import start_mock_server

        mock_server, server_url = start_mock_server()
        dev_skill_path = dev_skill_path or project_root / "tests" / "fixtures" / "skills" / "h3-prompt-writing"

    window = MainWindow(
        project_root=project_root,
        config_manager=config_manager,
        server_url=server_url,
        dev_skill_path=dev_skill_path,
        localization=localization,
    )
    window.show()

    if not args.skip_setup and not args.mock and not config.setup_completed:
        def show_setup() -> None:
            if SetupDialog(
                config_manager,
                project_root,
                window,
                enforce_portable_skill_storage=getattr(sys, "frozen", False),
                localization=localization,
            ).exec():
                window.config = config_manager.load()
                skill_path = (
                    Path(window.config.skill_location)
                    if window.config.skill_location
                    else config_manager.data_dir / "skills" / "h3-prompt-writing"
                )
                from core.skill_manager import SkillManager

                window.skill_manager = SkillManager(skill_path)
                window._refresh_readiness()
                window._refresh_memory_display()

        QTimer.singleShot(0, show_setup)
    if args.smoke_test:
        QTimer.singleShot(1200, app.quit)

    result = app.exec()
    window.server.stop()
    if mock_server is not None:
        mock_server.shutdown()
        mock_server.server_close()
    logging.info("Application stopped")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
