"""Where the app keeps its preferences (recent files, view toggles, window layout)."""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings

ENV_VAR = "OLAYOUT_SETTINGS_FILE"


def app_settings() -> QSettings:
    """The platform's settings store, or an INI file named by ``$OLAYOUT_SETTINGS_FILE``.

    Tests and scripts set the variable so they never touch the user's real
    preferences.  (``QSettings.setDefaultFormat`` does not affect the
    organization/application constructor, so it cannot be used for this.)
    """
    path = os.environ.get(ENV_VAR)
    if path:
        return QSettings(path, QSettings.Format.IniFormat)
    return QSettings("olayout", "olayout")
