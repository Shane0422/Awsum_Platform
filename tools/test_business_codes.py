"""Focused registration code tests; SQLite and temporary image storage only."""
from datetime import datetime
import json
import re
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.models_admin.merchant import Merchant
from backend.models_admin.agency import Agency
from backend.models_admin.business_type import BusinessType
from backend.models_admin.store import Store
from backend.routers import platform_store as routes
from backend.services import business_code
from tools import test_store_logo


class BusinessCodeTests(unittest.TestCase):
    sign_in = test_store_logo.OrganizationLogoTests.sign_in
    prepare_agency = test_store_logo.OrganizationLogoTests.prepare_agency

    def setUp(self):
        test_store_logo.OrganizationLogoTests.setUp(self)
        self.prepare_agency()
        self.db.add(BusinessType(i_business_type_id=1, c_business_code="TEST", c_name="Test"))
        self.db.commit()
        clock = patch.object(business_code, "datetime")
        self.clock = clock.start()
        self.clock.now.return_value = datetime(2026, 11, 2)
        self.addCleanup(clock.stop)

    def assert_code(self, code, prefix):
        self.assertRegex(code, rf"^{prefix}26[A-HJ-NP-Z2-9]{{4}}$")

    def test_registration_entry_points_use_common_codes_and_keep_existing_records(self):
        before = {
            "agency": (self.db.get(Agency, 1).c_agency_code, self.db.get(Agency, 1).c_logo_url),
            "merchant": (self.db.get(Merchant, 1).c_merchant_code, self.db.get(Merchant, 1).c_logo_url),
            "store": (self.db.get(Store, 2).c_store_code, self.db.get(Store, 2).i_merchant_id),
        }
        # A new Merchant is registered Inactive (Primary Contact first, then Active: platform_store activation rule).
        cases = (
            ("/platform/agency", {"company_name": "JSON Agency", "agency_type_id": 1}, "AGT", False),
            ("/platform/agency/with-image", {"company_name": "Image Agency", "agency_type_id": 1}, "AGT", True),
            ("/platform/merchant", {"merchant_name": "JSON Merchant", "primary_agency_id": 1, "business_type": "Test", "status": "inactive"}, "MER", False),
            ("/platform/merchant/with-image", {"merchant_name": "Image Merchant", "primary_agency_id": 1, "business_type": "Test", "status": "inactive"}, "MER", True),
            ("/platform/store", {"store_name": "Store", "merchant_id": 1, "installed_by_agency_id": 1}, "STR", False),
            ("/platform/store/with-image", {"store_name": "Image Store", "merchant_id": 1, "installed_by_agency_id": 1}, "STR", True),
            ("/platform/merchant/1/stores", {"store_name": "Linked Store"}, "STR", False),
        )
        codes = set()
        original_generator = business_code.generate_business_code
        with patch.object(routes, "generate_business_code", wraps=original_generator) as generator:
            for url, payload, prefix, multipart in cases:
                with self.subTest(url=url):
                    if multipart:
                        response = self.client.post(url, data={"payload": json.dumps(payload)},
                                                    files={"image": ("logo.png", self.blue, "image/png")})
                    else:
                        response = self.client.post(url, json=payload)
                    self.assertEqual(response.status_code, 200, response.text)
                    result = response.json()
                    identity = result.get("id", result.get("store_id"))
                    self.assertIsInstance(identity, int)
                    with self.factory() as db:
                        if prefix == "AGT":
                            code = db.get(Agency, identity).c_agency_code
                        elif prefix == "MER":
                            code = db.get(Merchant, identity).c_merchant_code
                        else:
                            store = db.get(Store, identity)
                            code = store.c_store_code
                            self.assertEqual(store.i_merchant_id, 1)
                        self.assert_code(code, prefix)
                        self.assertNotIn(code, codes)
                        codes.add(code)
                        if multipart:
                            self.assertIn(f"/{code}/", result["image_path"])
                            self.assertTrue(result["image_url"])
            self.assertEqual(generator.call_count, len(cases))
        self.db.expire_all()
        self.assertEqual(before["agency"], (self.db.get(Agency, 1).c_agency_code, self.db.get(Agency, 1).c_logo_url))
        self.assertEqual(before["merchant"], (self.db.get(Merchant, 1).c_merchant_code, self.db.get(Merchant, 1).c_logo_url))
        self.assertEqual(before["store"], (self.db.get(Store, 2).c_store_code, self.db.get(Store, 2).i_merchant_id))

    def test_collisions_retry_against_each_entity_without_changing_existing_codes(self):
        for entity, prefix, model, identity, field in (
            ("AGENCY", "AGT", Agency, 1, "c_agency_code"),
            ("MERCHANT", "MER", Merchant, 1, "c_merchant_code"),
            ("STORE", "STR", Store, 2, "c_store_code"),
        ):
            with self.subTest(entity=entity):
                row = self.db.get(model, identity)
                setattr(row, field, prefix + "26A7K3")
                self.db.commit()
                with patch.object(business_code.secrets, "choice", side_effect=list("A7K3F2M8")) as choice:
                    generated = business_code.generate_business_code(self.db, entity)
                self.assertEqual(generated, prefix + "26F2M8")
                self.assertEqual(choice.call_count, 8)
                self.assertEqual(getattr(row, field), prefix + "26A7K3")

    def test_registration_year_alphabet_and_explicit_exhaustion(self):
        self.assertEqual(set(business_code.BUSINESS_CODE_ALPHABET),
                         set("ABCDEFGHIJKLMNOPQRSTUVWXYZ23456789") - set("IO"))
        for year in (2026, 2027):
            self.clock.now.return_value = datetime(year, 1, 1)
            for entity, prefix in (("AGENCY", "AGT"), ("MERCHANT", "MER"), ("STORE", "STR")):
                self.assertTrue(re.fullmatch(prefix + str(year)[-2:] + r"[A-HJ-NP-Z2-9]{4}",
                                            business_code.generate_business_code(self.db, entity)))
        self.clock.now.return_value = datetime(2026, 11, 2)
        self.db.get(Agency, 1).c_agency_code = "AGT26AAAA"
        self.db.commit()
        with patch.object(business_code.secrets, "choice", return_value="A"):
            with self.assertRaises(HTTPException) as error:
                business_code.generate_business_code(self.db, "AGENCY")
        self.assertEqual(error.exception.status_code, 409)

    def test_postgresql_transaction_lock_is_shared_by_router_and_common_generators(self):
        db = MagicMock()
        db.get_bind.return_value.dialect.name = "postgresql"
        db.query.return_value.filter.return_value.first.return_value = None
        for function in (routes._generate_next_merchant_code,
                         lambda session: business_code.generate_business_code(session, "MERCHANT")):
            self.assert_code(function(db), "MER")
        self.assertEqual(db.execute.call_count, 2)
        self.assertEqual([call.args[1] for call in db.execute.call_args_list],
                         [{"lock_key": 11001002}, {"lock_key": 11001002}])


if __name__ == "__main__":
    unittest.main()
