"""Common Maintenance step 3 (API / service): group-scoped codes (COLOR view-only, UNIT CRUD), Code fixed after
save, case-insensitive Name duplicates, EA protection, in-use rules (MD Master + MD Detail), typed Values by the
group's Value Type, the Value Type lock and the per-scope codes (Platform / Merchant). SQLite only; no real database is touched."""
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.common_code_migration import migrate_common_codes
from backend.database.item_kind_migration import migrate_item_kinds
from backend.database.pg_platform import PlatformBase
from backend.models_admin.common_code import CommonCode, CommonGroup
from backend.models_admin.invoice import InvoiceLine
from backend.models_admin.master_group import Category1, Category2, Category3, ItemKind, MdDetail, MdMaster, Tax
from backend.models_admin.merchant import Merchant
from backend.models_admin.store import Store
from backend.models_admin.store_location import StoreLocation, StoreMdLocation
from backend.routers import master_group as router_module
from backend.services import master_group as svc

MODELS = (Tax, ItemKind, Category1, Category2, Category3, MdMaster, MdDetail, InvoiceLine, StoreLocation,
          StoreMdLocation, Store, Merchant, CommonGroup, CommonCode)


class CommonMaintenanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="awsum-common-maintenance-")
        self.addCleanup(directory.cleanup)
        storage = patch.object(svc.images, "image_storage_root", return_value=Path(directory.name))
        storage.start()
        self.addCleanup(storage.stop)
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        PlatformBase.metadata.create_all(self.engine, tables=[m.__table__ for m in MODELS])
        migrate_item_kinds(self.engine)
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            migrate_common_codes(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.db.close)

        def fake_user(request, db):
            return SimpleNamespace(i_platform_user_id=1, login_role=1)

        for target, value in (("get_current_user", fake_user), ("_is_super_admin", lambda db, user: False)):
            patcher = patch.object(router_module, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # ---- helpers ----
    def req(self, path="/platform/api/master-group"):
        return SimpleNamespace(cookies={"access_token": "x"}, headers={}, state=SimpleNamespace(),
                               url=SimpleNamespace(netloc="localhost:8000", path=path, query=""))

    def call(self, fn, *args, **kw):
        try:
            res = fn(*args, **kw)
        except HTTPException as exc:
            return exc.status_code, {"detail": exc.detail}
        return res.status_code, (json.loads(res.body) if res.headers.get("content-type", "").startswith("application/json") else res)

    def create(self, payload, group="UNIT"):
        return self.call(router_module.master_group_create, "common_code", payload, self.req(), self.db, group=group)

    def update(self, item_id, payload, group="UNIT"):
        return self.call(router_module.master_group_update, "common_code", item_id, payload, self.req(), self.db, group=group)

    def delete(self, item_id, group="UNIT"):
        return self.call(router_module.master_group_delete, "common_code", item_id, self.req(), self.db, group=group)

    def listing(self, group="UNIT", **kw):
        return self.call(router_module.master_group_list, "common_code", self.req(), db=self.db, group=group, **kw)

    def unit(self, code):
        return next(i for i in self.listing(size=100)[1]["items"] if i["code"] == code)

    def add_group(self, code, value_type, **settings):
        """A test-only group (direct insert) to exercise each Value Type; the screen shows only COLOR / UNIT."""
        values = dict(c_group_code=code, c_group_name=code.title(), c_code_mode="MANUAL", i_code_max_length=30,
                      c_value_type=value_type, c_value_label=f"{code.title()} Value", b_value_required=False,
                      b_merchant_extendable=False, i_display_order=90, b_active=True)
        group = CommonGroup(**{**values, **settings})
        self.db.add(group)
        self.db.commit()
        return group

    # ---- groups / list ----
    def test_only_color_and_unit_groups_and_group_scoped_list(self):
        code, options = self.call(router_module.master_group_options, self.req(), self.db)
        self.assertEqual(code, 200)
        groups = {g["code"]: g for g in options["common_groups"]}
        self.assertEqual(list(groups), ["COLOR", "UNIT"])
        self.assertEqual((groups["COLOR"]["value_type"], groups["COLOR"]["read_only"]), ("COLOR", False))
        self.assertEqual((groups["UNIT"]["value_type"], groups["UNIT"]["read_only"], groups["UNIT"]["code_mode"]),
                         (None, False, "MANUAL"))
        self.assertEqual(len(options["color_codes"]), 13)  # MD Color selector = COLOR codes
        self.assertNotIn("colors", options)  # the earlier tb_color list is gone (COLOR step 6)
        code, body = self.listing(size=100)
        self.assertEqual((code, body["total"]), (200, 14))
        self.assertEqual([i["code"] for i in body["items"]][:3], ["EA", "PK", "BOX"])
        code, body = self.listing("COLOR", size=100)
        self.assertEqual((code, body["total"], body["items"][0]["code"], body["items"][0]["value"]),
                         (200, 13, "CLR00001", "#000000"))  # new database: default colors seeded as COLOR codes
        self.assertNotIn("legacy_id", body["items"][0])
        for group in (None, "", "SIZE", "ITEM_KIND", "BOGUS"):  # SIZE / ITEM_KIND are never Common groups
            with self.subTest(group=group):
                self.assertEqual(self.listing(group)[0], 404)
        self.assertEqual(self.listing(q="pou")[1]["items"][0]["code"], "LB")

    def test_color_is_managed_here_and_the_color_api_is_gone(self):
        # COLOR step 6: COLOR codes live only in tb_common_code; the earlier Color API no longer exists.
        for status, _body in (
                self.call(router_module.master_group_list, "color", self.req(), db=self.db, size=100),
                self.call(router_module.master_group_create, "color", {"name": "Teal"}, self.req(), self.db),
                self.call(router_module.master_group_update, "color", 1, {"name": "Jet"}, self.req(), self.db),
                self.call(router_module.master_group_delete, "color", 13, self.req(), self.db)):
            self.assertEqual(status, 404)
        code, body = self.update(self.listing("COLOR")[1]["items"][0]["id"], {"name": "Jet", "value": "#000000"}, "COLOR")
        self.assertEqual((code, body["item"]["name"]), (200, "Jet"))
        with self.engine.connect() as conn:
            self.assertFalse(conn.execute(text("SELECT count(*) FROM sqlite_master WHERE name = 'tb_color'")).scalar())

    # ---- UNIT CRUD ----
    def test_unit_crud_code_fixed_and_duplicates_case_insensitive(self):
        code, body = self.create({"code": " ctn ", "name": "Carton", "description": "Box of boxes", "display_order": 150,
                                  "value": "ignored", "i_common_group_id": 1, "i_merchant_id": 9})
        self.assertEqual(code, 201)
        item = body["item"]
        self.assertEqual((item["code"], item["name"], item["group"], item["value"], item["active"]),
                         ("CTN", "Carton", "UNIT", None, True))
        row = self.db.get(CommonCode, item["id"])
        self.assertEqual((row.i_merchant_id, row.c_value_text, row.n_value), (None, None, None))  # never payload values
        code, body = self.update(item["id"], {"code": "XYZ", "name": "Carton Box", "display_order": 5})
        self.assertEqual((code, body["item"]["code"], body["item"]["name"]), (200, "CTN", "Carton Box"))
        for payload, field in (({"code": "ea", "name": "Each 2"}, "code"),        # code duplicate (case-insensitive)
                               ({"code": "NEW", "name": "each"}, "name"),         # name duplicate (case-insensitive)
                               ({"code": "A B", "name": "Spaced"}, "code"),       # invalid characters
                               ({"code": "", "name": "No code"}, "code"),
                               ({"code": "X" * 31, "name": "Long"}, "code"),
                               ({"code": "NONAME", "name": " "}, "name")):
            with self.subTest(payload=payload):
                status, body = self.create(payload)
                self.assertEqual(status, 422)
                self.assertIn(field, body["errors"])
        status, body = self.update(item["id"], {"name": "POUND"})
        self.assertEqual((status, list(body["errors"])), (422, ["name"]))
        self.assertEqual(self.update(item["id"], {"name": "Carton Box", "active": False})[0], 200)
        self.assertEqual(self.delete(item["id"])[0], 200)
        self.assertIsNone(self.db.get(CommonCode, item["id"]))

    def test_ea_cannot_be_deactivated_or_deleted(self):
        ea = self.unit("EA")
        self.assertEqual((ea["protected"], ea["active_locked"], ea["system"]), (True, True, True))
        status, body = self.update(ea["id"], {"name": "Each", "active": False})
        self.assertEqual((status, list(body["errors"])), (422, ["active"]))
        self.assertEqual(self.delete(ea["id"])[0], 409)
        self.assertEqual(self.update(ea["id"], {"name": "Each (piece)", "display_order": 1})[1]["item"]["name"], "Each (piece)")
        self.assertTrue(self.db.get(CommonCode, ea["id"]).b_active)

    def test_unit_in_use_by_md_master_or_md_detail(self):
        c1 = svc.create_item(self.db, "category1", {"name": "Hardware"}, 1)
        md = svc.create_item(self.db, "md", {"name": "Cable", "item_kind": "ITEM", "category1_id": c1["id"], "unit": "BOX",
                                             "retail_price": "1", "billing_cycle": "ONE_TIME"}, 1)
        svc.create_md_detail(self.db, md["id"], {"unit": "PK", "price": "2"}, 1)
        for code in ("BOX", "PK"):  # BOX: MD Master (and its Default Detail); PK: a non-default MD Detail only
            with self.subTest(code=code):
                row = self.unit(code)
                self.assertTrue(row["in_use"])
                self.assertEqual(self.delete(row["id"])[0], 409)
                self.assertEqual(self.update(row["id"], {"name": row["name"], "active": False})[0], 200)  # allowed (UI warns)
        self.assertFalse(self.unit("DZ")["in_use"])
        self.assertEqual(self.delete(self.unit("DZ")["id"])[0], 200)

    # ---- Value Types ----
    def test_value_types_text_number_currency_color(self):
        self.add_group("TXT", "TEXT", i_text_max_length=5)
        self.add_group("NUM", "NUMBER", i_value_scale=3, n_value_min=Decimal("-1"), n_value_max=Decimal("100"))
        self.add_group("CUR", "CURRENCY", b_value_required=True)
        self.add_group("HEX", "COLOR")
        cases = {
            "TXT": ([("abc", "abc"), ("", None)], ["abcdef"]),
            "NUM": ([("0", "0.000"), ("-1", "-1.000"), ("0.125", "0.125"), ("100", "100.000"), ("", None)],
                    ["1.2345", "100.001", "-1.5", "abc", "1,000", True]),
            "CUR": ([("2500.5", "2500.50"), ("0", "0.00")], ["", "1.234", "-1", "1,000.00"]),
            "HEX": ([("#1f2a44", "#1F2A44"), ("", None)], ["red", "#12345"]),
        }
        with patch.object(svc, "COMMON_MAINTENANCE_GROUPS", ("COLOR", "UNIT", "TXT", "NUM", "CUR", "HEX")):
            for group, (valid, invalid) in cases.items():
                for n, (raw, stored) in enumerate(valid):
                    with self.subTest(group=group, value=raw):
                        status, body = self.create({"code": f"V{n}", "name": f"Valid {n}", "value": raw}, group)
                        self.assertEqual((status, body["item"]["value"]), (201, stored))
                for n, raw in enumerate(invalid):
                    with self.subTest(group=group, value=raw):
                        status, body = self.create({"code": f"X{n}", "name": f"Invalid {n}", "value": raw}, group)
                        self.assertEqual((status, list(body["errors"])), (422, ["value"]))
            zero = self.db.query(CommonCode).filter(CommonCode.c_code == "V0", CommonCode.i_common_group_id ==
                                                    self.db.query(CommonGroup).filter_by(c_group_code="NUM").one().i_common_group_id).one()
            self.assertEqual((zero.n_value, zero.c_value_text), (Decimal("0"), None))  # 0 is a value, not NULL
            export = router_module.master_group_export("common_code", self.req(), db=self.db, group="CUR")
            self.assertTrue(export.body.decode("utf-8-sig").startswith("Code,Name,Description,Cur Value,Display Order"))
            self.assertIn("2,500.50", export.body.decode("utf-8-sig"))

    def test_value_type_cannot_change_once_a_group_has_codes(self):
        empty = self.add_group("EMPTY", None)
        self.assertEqual(svc.set_common_group_value_type(self.db, "EMPTY", "number", 1).c_value_type, "NUMBER")
        with self.assertRaises(svc.FieldErrors):
            svc.set_common_group_value_type(self.db, "EMPTY", "FLOAT", 1)
        for group, new in (("UNIT", "TEXT"), ("COLOR", None)):
            with self.subTest(group=group), self.assertRaises(svc.FieldErrors) as ctx:
                svc.set_common_group_value_type(self.db, group, new, 1)
            self.assertIn("value_type", ctx.exception.errors)
        self.db.refresh(empty)
        self.assertEqual(self.db.query(CommonGroup).filter_by(c_group_code="UNIT").one().c_value_type, None)
        self.assertEqual(svc.set_common_group_value_type(self.db, "UNIT", None, 1).c_value_type, None)  # same type: fine

    # ---- scope / pages ----
    def test_merchant_scope_has_its_own_codes(self):
        # STEP 1: Common Maintenance is per scope; a Merchant sees only its own codes (none of the Platform's).
        merchant = self.req("/merchant/api/master-group")
        merchant.state.master_merchant_id = 5
        self.assertEqual(router_module._entity("common_code", merchant), "common_code")
        self.assertEqual(svc.list_items(self.db, "common_code", merchant_id=5, group="UNIT")["total"], 0)
        # Item Kind stays Platform-only (403); MD Master is per owner since STEP 2 (tools.test_merchant_md_master).
        self.assertEqual(router_module._entity("md", merchant), "md")
        for entity in ("item_kind",):
            with self.assertRaises(HTTPException) as ctx:
                router_module._entity(entity, merchant)
            self.assertEqual(ctx.exception.status_code, 403)
        # A Merchant code stays in its scope: the Platform list and another Merchant never see it.
        svc.create_item(self.db, "common_code", {"code": "CTN", "name": "Carton"}, 1, merchant_id=5, group="UNIT")
        self.assertEqual([i["code"] for i in svc.list_items(self.db, "common_code", merchant_id=5, group="UNIT")["items"]],
                         ["CTN"])
        self.assertEqual(svc.list_items(self.db, "common_code", merchant_id=6, group="UNIT")["total"], 0)
        self.assertEqual(self.listing(size=100)[1]["total"], 14)
        self.assertNotIn("CTN", {i["code"] for i in self.listing(size=100)[1]["items"]})
        self.assertEqual(router_module.PAGES["common-maintenance"], ("common_code", "Common Maintenance"))
        self.assertNotIn("size", {e for e, _t in router_module.PAGES.values()})
        export = router_module.master_group_export("common_code", self.req(), db=self.db, group="UNIT")
        self.assertIn("master-common-maintenance-unit-", export.headers["content-disposition"])
        self.assertTrue(export.body.decode("utf-8-sig").startswith("Code,Name,Description,Display Order,Status,Notes"))



class MdUnitSelectorTests(CommonMaintenanceTests):
    """Step 4: MD Master / MD Detail Unit = Common UNIT code (c_unit text, no FK, same API field)."""
    test_only_color_and_unit_groups_and_group_scoped_list = None  # run once in the parent class
    test_color_is_managed_here_and_the_color_api_is_gone = None
    test_unit_crud_code_fixed_and_duplicates_case_insensitive = None
    test_ea_cannot_be_deactivated_or_deleted = None
    test_unit_in_use_by_md_master_or_md_detail = None
    test_value_types_text_number_currency_color = None
    test_value_type_cannot_change_once_a_group_has_codes = None
    test_merchant_scope_has_its_own_codes = None

    def md(self, unit="EA", **extra):
        c1 = svc.create_item(self.db, "category1", {"name": f"Cat {unit}"}, 1)
        payload = {"name": f"Item {unit}", "item_kind": "ITEM", "category1_id": c1["id"], "unit": unit,
                   "retail_price": "1", "billing_cycle": "ONE_TIME", **extra}
        return svc.create_item(self.db, "md", payload, 1)

    def md_errors(self, fn, *args):
        with self.assertRaises(svc.FieldErrors) as ctx:
            fn(*args)
        self.db.rollback()
        return ctx.exception.errors

    def set_raw_unit(self, md_id, unit):
        """An existing value saved before step 4 (inactive / not in the list): set directly, like legacy data."""
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE tb_md_master SET c_unit = :u WHERE i_md_id = :id"), {"u": unit, "id": md_id})
            conn.execute(text("UPDATE tb_md_detail SET c_unit = :u WHERE i_md_id = :id"), {"u": unit, "id": md_id})
        self.db.expire_all()

    def test_options_list_unit_codes_for_the_selector(self):
        units = self.call(router_module.master_group_options, self.req(), self.db)[1]["units"]
        self.assertEqual([u["code"] for u in units][:3], ["EA", "PK", "BOX"])
        self.assertEqual(units[0], {"code": "EA", "name": "Each", "active": True})
        pk = self.unit("PK")
        self.update(pk["id"], {"name": "Pack", "active": False})
        units = self.call(router_module.master_group_options, self.req(), self.db)[1]["units"]
        self.assertIn({"code": "PK", "name": "Pack", "active": False}, units)  # inactive kept for saved values

    def test_new_and_changed_units_must_be_active_codes(self):
        item = self.md("BOX")
        detail = svc.list_md_details(self.db, item["id"])[0]
        self.assertEqual((item["unit"], detail["unit"]), ("BOX", "BOX"))  # MD -> Default Detail sync unchanged
        self.assertEqual(self.md()["unit"], "EA")
        pk = self.unit("PK")
        self.update(pk["id"], {"name": "Pack", "active": False})
        for unit in ("PK", "BOTTLE", "box"):  # inactive / not a UNIT code / not the exact code
            with self.subTest(unit=unit):
                c1 = svc.create_item(self.db, "category1", {"name": f"C {unit}"}, 1)
                payload = {"name": f"New {unit}", "item_kind": "ITEM", "category1_id": c1["id"], "unit": unit,
                           "retail_price": "1", "billing_cycle": "ONE_TIME"}
                self.assertEqual(self.md_errors(svc.create_item, self.db, "md", payload, 1), {"unit": "Select an active Unit."})
                upd = {**svc.serialize(self.db, "md", self.db.get(MdMaster, item["id"])), "unit": unit}
                self.assertIn("unit", self.md_errors(svc.update_item, self.db, "md", item["id"], upd, 1))
                self.assertIn("unit", self.md_errors(svc.create_md_detail, self.db, item["id"], {"unit": unit, "price": "1"}, 1))
                self.assertIn("unit", self.md_errors(svc.update_md_detail, self.db, item["id"], detail["id"],
                                                     {"unit": unit, "price": "1", "is_default": True}, 1))
        changed = svc.update_md_detail(self.db, item["id"], detail["id"], {"unit": "CS", "price": "1", "is_default": True}, 1)
        self.assertEqual((changed["unit"], self.db.get(MdMaster, item["id"]).c_unit), ("CS", "CS"))  # Detail -> MD sync
        self.assertIn("unit", self.md_errors(svc.create_md_detail, self.db, item["id"], {"unit": "", "price": "1"}, 1))

    def test_unchanged_inactive_or_unlisted_saved_unit_is_kept(self):
        for saved in ("BOTTLE", "PK"):
            with self.subTest(saved=saved):
                item = self.md("EA")
                self.set_raw_unit(item["id"], saved)
                if saved == "PK":
                    self.update(self.unit("PK")["id"], {"name": "Pack", "active": False})
                row = svc.serialize(self.db, "md", self.db.get(MdMaster, item["id"]))
                saved_md = svc.update_item(self.db, "md", item["id"], {**row, "name": f"Renamed {saved}"}, 1)
                self.assertEqual((saved_md["unit"], saved_md["name"]), (saved, f"Renamed {saved}"))
                detail = svc.list_md_details(self.db, item["id"])[0]
                kept = svc.update_md_detail(self.db, item["id"], detail["id"],
                                            {"unit": saved, "price": "2", "is_default": True, "barcode": f"B-{saved}"}, 1)
                self.assertEqual((kept["unit"], kept["barcode"]), (saved, f"B-{saved}"))
                # A new Detail cannot take the inactive / unlisted value; changing to an active code works.
                self.assertIn("unit", self.md_errors(svc.create_md_detail, self.db, item["id"], {"unit": saved, "price": "1"}, 1))
                self.assertEqual(svc.update_item(self.db, "md", item["id"], {**row, "unit": "DZ"}, 1)["unit"], "DZ")

    def test_without_common_unit_group_the_text_rule_applies(self):
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE tb_common_group SET b_active = FALSE WHERE c_group_code = 'UNIT'"))
        self.db.expire_all()
        self.assertEqual(self.md("BOTTLE")["unit"], "BOTTLE")
        self.assertEqual(self.call(router_module.master_group_options, self.req(), self.db)[1]["units"], [])
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(engine.dispose)
        PlatformBase.metadata.create_all(engine, tables=[m.__table__ for m in MODELS if m not in (CommonGroup, CommonCode)])
        migrate_item_kinds(engine)
        db = sessionmaker(bind=engine)()
        self.addCleanup(db.close)
        self.assertIsNone(svc.active_unit_codes(db))
        c1 = svc.create_item(db, "category1", {"name": "Old DB"}, 1)
        item = svc.create_item(db, "md", {"name": "Legacy", "item_kind": "ITEM", "category1_id": c1["id"], "unit": "CASE12",
                                          "retail_price": "1", "billing_cycle": "ONE_TIME"}, 1)
        self.assertEqual(item["unit"], "CASE12")
        self.assertEqual(svc.lookup_options(db)["units"], [])


if __name__ == "__main__":
    unittest.main()
