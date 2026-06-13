import argparse
import unittest

from teslatron_services.launcher import REPO_ROOT, build_parser, resolve_launch_settings


class LauncherTests(unittest.TestCase):
    def test_default_profile_is_mock(self) -> None:
        parser = build_parser()

        args = parser.parse_args([])
        config_path, port = resolve_launch_settings(args)

        self.assertEqual(args.profile, "mock")
        self.assertEqual(config_path, REPO_ROOT / "config/cryostat_mock.json")
        self.assertEqual(port, 8765)

    def test_control_profile_uses_control_defaults(self) -> None:
        args = argparse.Namespace(profile="control", config=None, port=None)

        config_path, port = resolve_launch_settings(args)

        self.assertEqual(config_path, REPO_ROOT / "config/cryostat_lab_control.json")
        self.assertEqual(port, 8766)

    def test_custom_config_overrides_profile_config(self) -> None:
        args = argparse.Namespace(profile="mock", config="config/custom.json", port=9000)

        config_path, port = resolve_launch_settings(args)

        self.assertEqual(config_path, REPO_ROOT / "config/custom.json")
        self.assertEqual(port, 9000)
