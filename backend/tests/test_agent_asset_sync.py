from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from aiweb.agent.main import _needs_asset_sync


class AgentAssetSyncTests(unittest.TestCase):
    def test_missing_file_needs_sync(self) -> None:
        self.assertTrue(_needs_asset_sync("/definitely/missing.csv", "2026-07-23T12:00:00+00:00"))

    def test_same_or_older_server_version_does_not_overwrite(self) -> None:
        with tempfile.NamedTemporaryFile() as f:
            local_time = datetime.now(timezone.utc)
            os.utime(f.name, (local_time.timestamp(), local_time.timestamp()))

            self.assertFalse(_needs_asset_sync(f.name, local_time.isoformat()))
            self.assertFalse(_needs_asset_sync(f.name, (local_time - timedelta(seconds=1)).isoformat()))

    def test_newer_server_version_needs_sync(self) -> None:
        with tempfile.NamedTemporaryFile() as f:
            local_time = datetime.now(timezone.utc)
            os.utime(f.name, (local_time.timestamp(), local_time.timestamp()))

            self.assertTrue(_needs_asset_sync(f.name, (local_time + timedelta(seconds=1)).isoformat()))


if __name__ == "__main__":
    unittest.main()
