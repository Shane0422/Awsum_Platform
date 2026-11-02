"""Merchant contracts against an isolated database and the existing Common services."""
import ast
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text

from backend.models_admin.business_type import BusinessType
from backend.models_admin.merchant import Merchant
from backend.models_admin.store import Store
from backend.routers import platform_store
from backend.services.business_contact import BusinessContactService, OWNER_MODELS
from tools import test_master_images


class MerchantTests(unittest.TestCase):
    sign_in = test_master_images.MasterImageTests.sign_in

    def setUp(self):
        test_master_images.MasterImageTests.setUp(self)
        self.db.add(BusinessType(c_business_code="TEST", c_name="Test", c_status="active"))
        self.db.commit()
        self.payload = {"merchant_name": "New Merchant", "primary_agency_id": 1,
                        "business_type": "Test", "website": "example.test", "notes": "Original",
                        "status": "inactive"}
        # Mirror the PostgreSQL partial index in the SQLite test database.
        with self.engine.begin() as connection:
            connection.execute(text("DROP INDEX ux_business_contact_primary"))
            connection.execute(text(
                "CREATE UNIQUE INDEX ux_business_contact_primary ON tb_business_contact "
                "(c_owner_type, i_owner_id) WHERE b_is_primary"))

    def create(self):
        response = self.client.post("/platform/merchant", json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def test_json_crud_search_and_validation(self):
        identity = self.create()
        self.assertEqual(self.client.post("/platform/merchant", json=self.payload).status_code, 409)
        self.assertEqual(self.client.post("/platform/merchant", json={}).status_code, 400)
        self.assertEqual(self.client.post("/platform/client", json=self.payload).status_code, 404)
        update = self.client.put(f"/platform/merchant/{identity}", json={
            "merchant_name": "Renamed Merchant", "merchant_code": "IGNORED",
            "website": "updated.example.test", "notes": "Updated", "phone": "202-555-0101"})
        self.assertEqual(update.status_code, 200, update.text)
        listed = self.client.get("/platform/merchants")
        self.assertEqual(listed.status_code, 200, listed.text)
        row = next(item for item in listed.json() if item["id"] == identity)
        self.assertEqual(row["merchant_name"], "Renamed Merchant")
        self.assertEqual(row["notes"], "Updated")
        self.assertNotEqual(row["merchant_code"], "IGNORED")
        self.assertNotIn("client_name", row)
        self.assertNotIn("account_id", row)
        search = self.client.get("/platform/merchants", params={"q": "Renamed Merchant"})
        self.assertEqual([item["id"] for item in search.json()], [identity])
        bad = self.client.put(f"/platform/merchant/{identity}", json={"email": "invalid"})
        self.assertEqual(bad.status_code, 400)
        with self.factory() as db:
            self.assertEqual(db.get(Merchant, identity).c_merchant_name, "Renamed Merchant")

    def test_store_relation_and_existing_delete_policy(self):
        identity = self.create()
        linked = self.client.post(f"/platform/merchant/{identity}/stores",
                                  json={"store_name": "Linked Store", "store_status": "inactive"})
        self.assertEqual(linked.status_code, 200, linked.text)
        with self.factory() as db:
            store = db.get(Store, linked.json()["store_id"])
            self.assertEqual(store.i_merchant_id, identity)
            self.assertIsNone(store.i_installed_by_agency_id)
        listed = self.client.get(f"/platform/merchant/{identity}/stores")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertIn(linked.json()["store_id"], [row["store_id"] for row in listed.json()])
        # Only the PostgreSQL catalog query is stubbed; reference counts and mutations are real.
        with self.factory() as db:
            execute = db.execute

            def catalog(statement, *args, **kwargs):
                if "pg_constraint" in str(statement):
                    class References:
                        def all(self):
                            return [("tb_store", "i_merchant_id")]
                    return References()
                return execute(statement, *args, **kwargs)

            with patch.object(db, "execute", side_effect=catalog):
                response = platform_store.delete_merchant(identity, self._request(), db)
                self.assertTrue(json.loads(response.body)["deactivated"])
                self.assertEqual(db.get(Merchant, identity).c_status, "inactive")
                self.assertEqual(db.get(Store, linked.json()["store_id"]).i_merchant_id, identity)
                empty = Merchant(c_merchant_name="Unused", c_merchant_code="MER26A7K3")
                db.add(empty)
                db.commit()
                empty_id = empty.i_merchant_id
                response = platform_store.delete_merchant(empty_id, self._request(), db)
                self.assertTrue(json.loads(response.body)["deleted"])
                self.assertIsNone(db.get(Merchant, empty_id))

    def _request(self):
        from tools.test_web_sessions import request
        return request(self.tokens[1], path="/platform/merchant")

    def test_common_contacts_all_three_owners(self):
        self.assertEqual(OWNER_MODELS["MERCHANT"], (Merchant, "i_merchant_id"))
        service = BusinessContactService(self.db)
        for owner, identity in (("AGENCY", 1), ("MERCHANT", 1), ("STORE", 2)):
            with self.subTest(owner=owner):
                first = service.create_contact(owner, identity, {
                    "first_name": "First", "last_name": "Contact", "email": "one@example.test"})
                second = service.create_contact(owner, identity, {
                    "first_name": "Second", "last_name": "Contact", "email": "two@example.test"})
                service.set_primary(second["contact_id"])
                self.assertEqual(service.primary_names(owner, [identity])[identity], "Second Contact")
                service.update_contact(first["contact_id"], {
                    "first_name": "Updated", "last_name": "Contact", "email": "one@example.test"})
                service.reorder(owner, identity, [second["contact_id"], first["contact_id"]])
                contacts = service.get_contacts(owner, identity)
                self.assertEqual([row["contact_id"] for row in contacts],
                                 [second["contact_id"], first["contact_id"]])
                self.assertEqual(sum(row["is_primary"] for row in contacts), 1)
                service.delete_contact(first["contact_id"])
                self.assertEqual(len(service.get_contacts(owner, identity)), 1)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for full page JavaScript checks.")
    def test_master_pages_have_valid_javascript_and_existing_store_panel(self):
        for module in ("merchant", "agency", "store"):
            response = self.client.get(f"/platform/master/{module}")
            self.assertEqual(response.status_code, 200, response.text[:200])
            scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", response.text, re.S | re.I)
            for attrs, source in scripts:
                if "src=" in attrs or "application/json" in attrs:
                    continue
                result = subprocess.run(["node", "--check"], input=source, text=True, encoding="utf-8",
                                        capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
            if module == "merchant":
                self.assertIn("{ id: 'stores', label: 'Stores'", response.text)
                self.assertIn("field_merchant_code", response.text)
                self.assertIn("initMerchantStorePanelHandlers", response.text)

    def test_existing_merchant_prevents_bootstrap_duplication(self):
        source = Path(r"backend\database\db_init_platform.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        seed = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                    and ast.get_source_segment(source, node.test) == "not db.query(Merchant).first()")
        with patch.object(self.db, "add", wraps=self.db.add) as add:
            exec(compile(ast.Module(body=[seed], type_ignores=[]), "<merchant-seed>", "exec"),
                 {"db": self.db, "Merchant": Merchant})
            add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
