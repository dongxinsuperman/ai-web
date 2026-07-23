from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aiweb.scheduler.service import parse_and_validate


class FakeResult:
    def __init__(self, values: list[str]) -> None:
        self.values = values

    def scalars(self):
        return self

    def all(self) -> list[str]:
        return self.values


class FakeSession:
    def __init__(self, asset_names: list[str]) -> None:
        self.asset_names = asset_names
        self.added: list[object] = []

    async def execute(self, _statement):
        return FakeResult(self.asset_names)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None


class SubmissionAssetValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_asset_name_is_accepted(self) -> None:
        session = FakeSession(["source.csv"])
        payload = {
            "items": [{
                "caseId": "upload-source",
                "runContent": "上传 source.csv",
                "assets": ["source.csv"],
            }],
        }

        with (
            patch("aiweb.scheduler.service.get_slots", return_value={"chrome": 1}),
            patch(
                "aiweb.scheduler.service.get_settings",
                return_value=SimpleNamespace(function_map_context_max_chars=0),
            ),
        ):
            await parse_and_validate(session, payload)

        item = session.added[-1]
        self.assertEqual(item.assets, ["source.csv"])


if __name__ == "__main__":
    unittest.main()
