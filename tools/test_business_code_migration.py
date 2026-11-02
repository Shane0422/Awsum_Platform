import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.data_paths import get_data_root
from backend.database.ops_db_provision import build_ops_db_name
from tools.migrate_business_codes import (
    _find_code_conflicts,
    replace_code_tokens,
    transform_code,
)


class BusinessCodeMigrationTests(unittest.TestCase):
    def test_removes_separator_and_preserves_prefix_and_digits(self):
        examples = (
            ("CLT_11001", ("CLT",), "CLT11001"),
            ("CLT_00001", ("CLT",), "CLT00001"),
            ("STR_00001", ("STR",), "STR00001"),
            ("AGT_00001", ("AGT",), "AGT00001"),
            ("MER_00001", ("MER",), "MER00001"),
            ("CUST_10001", ("CUST",), "CUST10001"),
        )
        for old_code, prefixes, expected in examples:
            with self.subTest(old_code=old_code):
                self.assertEqual(transform_code(old_code, prefixes), expected)

    def test_does_not_transform_other_identifiers(self):
        self.assertIsNone(transform_code("POS_123456789", ("CLT", "STR", "AGT")))
        self.assertIsNone(transform_code("DeviceFingerprint_ABC", ("CLT", "STR", "AGT")))
        self.assertIsNone(transform_code("CLT11001", ("CLT",)))

    def test_detects_collision_before_conversion(self):
        conflicts = _find_code_conflicts(
            "tb_merchant",
            [(1, "CLT_11001"), (2, "CLT11001")],
            ("CLT",),
        )
        self.assertEqual(len(conflicts), 1)
        self.assertIn("CLT_11001 -> CLT11001", conflicts[0])

    def test_path_replacement_only_changes_complete_code_tokens(self):
        mappings = [{"old_code": "CLT_11001", "new_code": "CLT11001"}]
        self.assertEqual(
            replace_code_tokens(
                r"C:\data\company\CLT_11001\main.db; CLT_110010",
                mappings,
            ),
            r"C:\data\company\CLT11001\main.db; CLT_110010",
        )

    def test_store_data_root_honors_environment_setting(self):
        with patch.dict(os.environ, {"AWSUM_STORAGE_ROOT": r"C:\AwsumData", "AWSUM_DATA_ROOT": ""}):
            self.assertEqual(get_data_root(), Path(r"C:\AwsumData").resolve())

    def test_tenant_database_names_have_no_separator(self):
        self.assertEqual(build_ops_db_name("CUST", 1), "CUST00001")
        self.assertEqual(build_ops_db_name("STORE", 20001), "STORE20001")


if __name__ == "__main__":
    unittest.main()
