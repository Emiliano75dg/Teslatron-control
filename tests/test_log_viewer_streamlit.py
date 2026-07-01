import unittest
from pathlib import Path
from unittest import mock

from tools.inspect_environment_log_streamlit import (
    current_day_uploaded_names,
    refreshable_current_day_paths,
)


class LogViewerStreamlitTests(unittest.TestCase):
    def test_current_day_uploaded_names_returns_only_today_variants(self) -> None:
        names = [
            "cryostat_environment_2026-06-29.csv",
            "cryostat_environment_2026-06-29_v2.csv",
            "cryostat_environment_2026-06-28.csv",
        ]

        fake_now = mock.Mock()
        fake_now.strftime.return_value = "2026-06-29"

        with mock.patch("tools.inspect_environment_log_streamlit.datetime") as fake_datetime:
            fake_datetime.now.return_value = fake_now
            selected = current_day_uploaded_names(names)

        self.assertEqual(
            selected,
            [
                "cryostat_environment_2026-06-29.csv",
                "cryostat_environment_2026-06-29_v2.csv",
            ],
        )

    def test_refreshable_current_day_paths_returns_disk_matches_for_uploaded_names(self) -> None:
        uploaded_names = [
            "cryostat_environment_2026-06-30.csv",
            "cryostat_environment_2026-06-30_v2.csv",
            "cryostat_environment_2026-06-29.csv",
        ]
        fake_now = mock.Mock()
        fake_now.strftime.return_value = "2026-06-30"

        with mock.patch("tools.inspect_environment_log_streamlit.datetime") as fake_datetime:
            fake_datetime.now.return_value = fake_now
            with mock.patch(
                "tools.inspect_environment_log_streamlit.discover_directory_files",
                return_value=[
                    Path("/tmp/cryostat_environment_2026-06-30.csv"),
                    Path("/tmp/cryostat_environment_2026-06-30_v2.csv"),
                    Path("/tmp/cryostat_environment_2026-06-28.csv"),
                ],
            ):
                selected = refreshable_current_day_paths(uploaded_names, Path("/tmp"))

        self.assertEqual(
            selected,
            [
                Path("/tmp/cryostat_environment_2026-06-30.csv"),
                Path("/tmp/cryostat_environment_2026-06-30_v2.csv"),
            ],
        )
