import io
import unittest

import pandas as pd

from tools.environment_log_reader import format_float, load_and_prepare_logs, values_differ


class EnvironmentLogReaderTests(unittest.TestCase):
    def test_numeric_helpers_treat_blank_strings_as_missing_or_non_numeric(self) -> None:
        self.assertIsNone(format_float(""))
        self.assertFalse(values_differ("", "", threshold=0.15))
        self.assertTrue(values_differ("", "4.2", threshold=0.15))

    def test_load_and_prepare_logs_reorders_known_columns_for_display(self) -> None:
        csv_data = io.StringIO(
            "\n".join(
                [
                    "timestamp,mode,backend,pressure_mode,sample_temperature_K,pressure_mbar,safety_level",
                    "1710000000,idle,standard,auto,4.2,0.01,ok",
                ]
            )
        )
        csv_data.name = "legacy.csv"

        df, time_col, filtered_rows_total = load_and_prepare_logs([csv_data])

        self.assertEqual(time_col, "timestamp")
        self.assertEqual(filtered_rows_total, 0)
        self.assertLess(df.columns.get_loc("sample_temperature_K"), df.columns.get_loc("mode"))
        self.assertLess(df.columns.get_loc("pressure_mbar"), df.columns.get_loc("pressure_mode"))
        self.assertTrue(pd.api.types.is_numeric_dtype(df["sample_temperature_K"]))
        self.assertTrue(pd.api.types.is_numeric_dtype(df["pressure_mbar"]))

    def test_load_and_prepare_logs_accepts_mixed_legacy_and_current_row_layouts(self) -> None:
        csv_data = io.StringIO(
            "\n".join(
                [
                    ",".join(
                        [
                            "timestamp",
                            "mode",
                            "backend",
                            "sample_temperature_K",
                            "sample_target_K",
                            "sample_rate_K_per_min",
                            "sample_ramp_end_K",
                            "sample_heater_percent",
                            "sample_heater_power_W",
                            "sample_heater_voltage_V",
                            "sample_mode",
                            "sample_stable",
                            "sample_ramping",
                            "vti_temperature_K",
                            "vti_target_K",
                            "vti_rate_K_per_min",
                            "vti_ramp_end_K",
                            "vti_heater_percent",
                            "vti_heater_power_W",
                            "vti_heater_voltage_V",
                            "vti_mode",
                            "vti_stable",
                            "vti_ramping",
                            "B_T",
                            "field_target_T",
                            "field_rate_T_per_min",
                            "field_output_current_A",
                            "field_output_voltage_V",
                            "magnet_temperature_K",
                            "pt1_temperature_K",
                            "pt2_temperature_K",
                            "field_stable",
                            "field_ramping",
                            "switch_heater_status",
                            "switch_heater_target_status",
                            "switch_heater_ready",
                            "switch_heater_delay_s",
                            "switch_heater_elapsed_s",
                            "pressure_mbar",
                            "pressure_target_mbar",
                            "needle_valve_percent",
                            "pressure_mode",
                            "safety_level",
                            "safety_message",
                        ]
                    ),
                    ",".join(
                        [
                            "1710000000",
                            "idle",
                            "standard",
                            "4.2",
                            "4.2",
                            "0.5",
                            "4.2",
                            "10",
                            "0.1",
                            "",
                            "FIXED_TARGET",
                            "True",
                            "False",
                            "5.1",
                            "5.1",
                            "0.5",
                            "5.1",
                            "11",
                            "0.2",
                            "",
                            "FIXED_TARGET",
                            "True",
                            "False",
                            "1.0",
                            "1.0",
                            "0.2",
                            "5.0",
                            "0.1",
                            "4.0",
                            "3.0",
                            "2.0",
                            "True",
                            "False",
                            "OFF",
                            "OFF",
                            "True",
                            "300",
                            "",
                            "0.01",
                            "",
                            "0",
                            "FIXED_NEEDLE",
                            "ok",
                            "",
                        ]
                    ),
                    ",".join(
                        [
                            "1710000060",
                            "4.3",
                            "4.5",
                            "0.6",
                            "4.5",
                            "12",
                            "0.11",
                            "",
                            "5.2",
                            "5.4",
                            "0.6",
                            "5.4",
                            "13",
                            "0.21",
                            "",
                            "1.1",
                            "1.2",
                            "0.25",
                            "5.1",
                            "0.11",
                            "4.1",
                            "3.1",
                            "2.1",
                            "300",
                            "",
                            "0.02",
                            "",
                            "0",
                            "idle",
                            "standard",
                            "PID_AUTO",
                            "True",
                            "False",
                            "True",
                            "FIXED_TARGET",
                            "True",
                            "False",
                            "PID_AUTO",
                            "True",
                            "False",
                            "True",
                            "FIXED_TARGET",
                            "True",
                            "False",
                            "HOLD",
                            "True",
                            "False",
                            "False",
                            "True",
                            "False",
                            "OFF",
                            "OFF",
                            "True",
                            "FIXED_NEEDLE",
                            "ok",
                            "",
                        ]
                    ),
                ]
            )
        )
        csv_data.name = "mixed.csv"

        df, time_col, filtered_rows_total = load_and_prepare_logs([csv_data])

        self.assertEqual(time_col, "timestamp")
        self.assertEqual(filtered_rows_total, 0)
        self.assertEqual(len(df), 2)
        self.assertIn("field_action", df.columns)
        self.assertEqual(str(df.iloc[-1]["field_action"]), "HOLD")
        self.assertTrue(pd.api.types.is_numeric_dtype(df["sample_temperature_K"]))
        self.assertTrue(pd.api.types.is_numeric_dtype(df["B_T"]))
