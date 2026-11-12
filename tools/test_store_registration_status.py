"""Store registration (Agency + Merchant + Store Name, saved Inactive), Active-change checks, and the common Store
Status rule: a Merchant views an Inactive Store read-only (enforced by the API); Platform manages every Store."""
import unittest

from fastapi.testclient import TestClient

from backend.models_admin.agency import Agency
from backend.models_admin.business_contact import BusinessContact
from backend.models_admin.platform_user import PlatformUser
from backend.models_admin.store import Store
from backend.routers import master_group, platform_store
from backend.services.app_navigation import STORE_INACTIVE_READ_ONLY
from backend.services.organization_users import organization_admin_query
from tools import test_registration_email

LOC = "/{p}/api/master-group/location"


class StoreRegistrationStatusTests(unittest.TestCase):
    def setUp(self):
        test_registration_email.RegistrationEmailTests.setUp(self)
        self.app.include_router(master_group.router)
        self.app.dependency_overrides[master_group.get_db] = self.app.dependency_overrides[platform_store.get_db]
        with self.factory() as db:
            db.add(Agency(i_agency_id=2, i_agency_type_id=1, c_agency_code="AGT00002", c_agency_name="Agency Two",
                          c_company_name="Agency Two", c_status="active"))
            db.commit()

    def register(self, **extra):
        return self.client.post("/platform/store", json={"store_name": "New Store", "merchant_id": 10,
                                                          "installed_by_agency_id": 2, **extra})

    def store(self, store_id):
        with self.factory() as db:
            return db.get(Store, store_id)

    def add_primary_contact(self, store_id):
        with self.factory() as db:
            db.add(BusinessContact(c_owner_type="STORE", i_owner_id=store_id, c_contact_role="PRIMARY", c_first_name="Lee",
                                   c_last_name="Store", c_email=f"store{store_id}@example.test", c_phone="703-555-0101",
                                   b_is_primary=True, c_status="active"))
            db.commit()

    def merchant_client(self):
        test_registration_email.RegistrationEmailTests.activate(self, "MERCHANT")
        client = TestClient(self.app)
        response = client.post("/auth/login", data={"store_code": "MER26ABCD", "email": "primary@example.test",
                                                    "password": "Awsum@5858"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        return client

    # ---- registration ----
    def test_three_fields_register_an_inactive_store_with_generated_code(self):
        response = self.register()  # Merchant 10 belongs to Agency 1; the Store's Agency 2 is allowed
        self.assertEqual(response.status_code, 200, response.text)
        created = self.store(response.json()["store_id"])
        self.assertEqual((created.c_status, created.c_store_name, created.i_installed_by_agency_id, created.i_business_type_id),
                         ("inactive", "New Store", 2, None))
        self.assertTrue(created.c_store_code and created.i_store_seq)
        self.assertNotIn("store_user_created", response.json())  # no Store Admin login while Inactive
        with self.factory() as db:
            self.assertIsNone(organization_admin_query(db, "STORE", created.i_store_id).first())
        for missing, message in (("store_name", "Store name is required."), ("merchant_id", "Merchant is required."),
                                 ("installed_by_agency_id", "Agency is required.")):
            bad = self.register(**{missing: ""})
            self.assertEqual((bad.status_code, bad.json()["detail"]), (400, message))

    def test_active_change_checks_existing_conditions_and_edit_completes_the_store(self):
        refused = self.register(store_status="active")  # created as Active without a Primary Contact: refused
        self.assertEqual(refused.status_code, 400)
        self.assertIn("complete: Primary Contact", refused.json()["detail"])
        with self.factory() as db:
            self.assertEqual(db.query(Store).filter(Store.c_store_name == "New Store").count(), 0)
        store_id = self.register().json()["store_id"]
        # Edit fills the rest while Inactive.
        edited = self.client.put(f"/platform/store/{store_id}", json={"business_type": "", "city": "Atlanta", "phone": "404-555-0100"})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual((self.store(store_id).c_city, self.store(store_id).c_status), ("Atlanta", "inactive"))
        blocked = self.client.put(f"/platform/store/{store_id}", json={"store_status": "active"})
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.json()["detail"], "To make this Store Active, complete: Primary Contact. "
                                                   "Save it as Inactive, add them in Edit, then change Status to Active.")
        self.assertEqual(self.store(store_id).c_status, "inactive")
        self.add_primary_contact(store_id)
        activated = self.client.put(f"/platform/store/{store_id}", json={"store_status": "active"})
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertTrue(activated.json()["store_user_created"])
        self.assertEqual(self.store(store_id).c_status, "active")

    def test_editing_an_active_store_is_unchanged(self):
        with self.factory() as db:  # an earlier active Store with a Store Admin login but no Primary Contact any more
            db.query(BusinessContact).filter_by(c_owner_type="STORE").delete()
            store = db.get(Store, 10)
            store.c_status = "active"
            db.add(PlatformUser(i_store_id=10, i_role_id=organization_role_id(db), c_email="old@example.test",
                                c_password="x", c_status="active"))
            db.commit()
        response = self.client.put("/platform/store/10", json={"store_name": "Renamed", "store_status": "active"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store(10).c_store_name, "Renamed")

    # ---- Store Status: Merchant read-only, Platform manages ----
    def test_merchant_views_an_inactive_store_read_only_until_platform_activates_it(self):
        # STEP 3-3C: Location Master needs the Merchant's Use Location Management Business Option (ADDON).
        with self.factory() as db:
            from backend.services.merchant_features import save_business_options
            save_business_options(db, 10, {"location_management": True}, 1)
            db.commit()
        made = self.client.post(LOC.format(p="platform") + "?store_id=10", json={"code": "S01-B01-C01", "location_area": "SALES_FLOOR"})
        self.assertEqual(made.status_code, 201, made.text)  # Platform manages an Inactive Store
        location_id = made.json()["item"]["id"]
        merchant = self.merchant_client()
        options = merchant.get("/merchant/api/master-group/options").json()
        store = next(s for s in options["stores"] if s["id"] == 10)
        self.assertEqual((store["active"], store["read_only"], store["read_only_message"]), (False, True, STORE_INACTIVE_READ_ONLY))
        listed = merchant.get(LOC.format(p="merchant"), params={"store_id": 10})
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual([(i["code"], i["read_only"]) for i in listed.json()["items"]], [("S01-B01-C01", True)])
        self.assertEqual(merchant.get(LOC.format(p="merchant") + "/codes", params={"store_id": 10, "location_area": "SALES_FLOOR"}).status_code, 200)
        base = LOC.format(p="merchant")
        writes = (
            ("post", base + "?store_id=10", {"code": "S01-B01-C02", "location_area": "SALES_FLOOR"}),
            ("put", f"{base}/{location_id}?store_id=10", {"code": "S01-B01-C01", "location_area": "SALES_FLOOR", "name": "x"}),
            ("delete", f"{base}/{location_id}?store_id=10", None),
            ("post", base + "/initialize?store_id=10", {}),
            ("post", base + "/range?store_id=10", {"location_area": "SALES_FLOOR", "zone": "S01", "bay_from": 1, "bay_to": 1,
                                                   "shelf_from": 1, "shelf_to": 1}),
        )
        for method, url, body in writes:
            response = getattr(merchant, method)(url, **({"json": body} if body is not None else {}))
            self.assertEqual((response.status_code, response.json()["detail"]), (403, STORE_INACTIVE_READ_ONLY), url)
        self.assertEqual(self.store(10).c_status, "inactive")
        # Platform activates it (existing condition: the Primary Contact from the fixture) -> the Merchant manages it.
        test_registration_email.RegistrationEmailTests.activate(self, "STORE")
        store = next(s for s in merchant.get("/merchant/api/master-group/options").json()["stores"] if s["id"] == 10)
        self.assertEqual((store["active"], store["read_only"]), (True, False))
        self.assertNotIn("read_only", merchant.get(LOC.format(p="merchant"), params={"store_id": 10}).json()["items"][0])
        self.assertEqual(merchant.post(base + "?store_id=10", json={"code": "S01-B01-C02", "location_area": "SALES_FLOOR"}).status_code, 201)

    def test_merchant_store_list_shows_inactive_stores_without_location_data(self):
        new_id = self.register().json()["store_id"]  # Inactive, no Location data
        merchant = self.merchant_client()
        stores = {s["id"]: s for s in merchant.get("/merchant/api/master-group/options").json()["stores"]}
        self.assertTrue({10, new_id} <= set(stores))
        self.assertTrue(stores[new_id]["read_only"])
        # Another Merchant's Store stays out of scope (existing rule).
        with self.factory() as db:
            db.add(Store(i_store_id=77, i_merchant_id=None, c_store_code="OTHER", c_store_name="Other", c_status="active"))
            db.commit()
        self.assertEqual(merchant.get(LOC.format(p="merchant"), params={"store_id": 77}).status_code, 403)


def organization_role_id(db):
    from backend.models_admin.platform_user import STORE_ADMIN_ROLE_NAME
    from backend.models_admin.role import Role
    return db.query(Role).filter(Role.c_name == STORE_ADMIN_ROLE_NAME).one().i_role_id


if __name__ == "__main__":
    unittest.main()
