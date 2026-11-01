"""Okapi sidecar: the download URLs name the version the client expects.

Users who installed Workbench with pip get the sidecar downloaded from a
GitHub release, stamped with EXPECTED_VERSION (okapi-sidecar.version). If
EXPECTED_VERSION is bumped but these URLs are not, the old files are fetched
and stamped as the new version, so they are never refreshed again and the
user quietly keeps the old sidecar's behaviour.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.okapi_sidecar import OkapiSidecar  # noqa: E402


def test_download_urls_name_the_expected_version():
    v = OkapiSidecar.EXPECTED_VERSION
    win, jar = OkapiSidecar.INSTALLER_URL_WINDOWS, OkapiSidecar.INSTALLER_URL_JAR_ONLY
    assert win.endswith(f"/okapi-sidecar-windows-v{v}.zip")
    assert jar.endswith(f"/okapi-sidecar-v{v}.jar")
    # Both assets are attached to the same GitHub release.
    assert win.rsplit("/", 1)[0] == jar.rsplit("/", 1)[0]
    assert "/releases/download/v" in win
