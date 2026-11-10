"""COLOR after step 6: colors are COLOR codes of Common Maintenance only (no tb_color). MD Detail stores the COLOR
code (color_code_id); the removed tb_color id (color_id) is refused with 422 whatever its value, also for a Store
with Use Color OFF. The earlier Color API is gone (404) and its page URL still redirects (307) to Common
Maintenance?group=COLOR. SQLite only; no real database is touched."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
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
from backend.services.store_item_features import without_disabled_item_features

MODELS = (Tax, ItemKind, Category1, Category2, Category3, MdMaster, MdDetail, InvoiceLine, StoreLocation,
          StoreMdLocation, Store, Merchant, CommonGroup, CommonCode)


class ColorCodeOnlyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="awsum-color-code-")
        self.addCleanup(directory.cleanup)
        storage = patch.object(svc.images, "image_storage_root", return_value=Path(directory.name))
        storage.start()
        self.addCleanup(storage.stop)
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        PlatformBase.metadata.create_all(self.engine, tables=[m.__table__ for m in MODELS])
        migrate_item_kinds(self.engine)
        with redirect_stdout(io.StringIO()):
            migrate_common_codes(self.engine)  # COLOR codes CLR00001.. (ids 1-13), UNIT codes after them
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.db.close)

        def fake_user(request, db):
            return SimpleNamespace(i_platform_user_id=1, login_role=1, i_store_id=None)

        for target, value in (("get_current_user", fake_user), ("_is_super_admin", lambda db, user: False)):
            patcher = patch.object(router_module, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # ---- helpers ----
    def req(self, path="/platform/api/master-group", query=""):
        return SimpleNamespace(cookies={"access_token": "x"}, headers={}, state=SimpleNamespace(),
                               url=SimpleNamespace(netloc="localhost:8000", path=path, query=query))

    def call(self, fn, *args, **kw):
        try:
            res = fn(*args, **kw)
        except HTTPException as exc:
            return exc.status_code, {"detail": exc.detail}
        return res.status_code, json.loads(res.body)

    def common(self, method, *args, **kw):
        fn = {"create": router_module.master_group_create, "update": router_module.master_group_update,
              "delete": router_module.master_group_delete}[method]
        return self.call(fn, "common_code", *args, self.req(), self.db, group="COLOR", **kw)

    def color(self, code):
        items = self.call(router_module.master_group_list, "common_code", self.req(), db=self.db, group="COLOR",
                          size=100)[1]["items"]
        return next(i for i in items if i["code"] == code)

    def form(self, row, **changes):
        return {"name": row["name"], "value": row["value"], "description": row["description"],
                "display_order": row["display_order"], "active": row["active"], "notes": row["notes"], **changes}

    def md(self):
        c1 = svc.create_item(self.db, "category1", {"name": "Apparel"}, 1)
        return svc.create_item(self.db, "md", {"name": "Tee", "item_kind": "ITEM", "category1_id": c1["id"], "unit": "EA",
                                               "retail_price": "0", "billing_cycle": "ONE_TIME"}, 1)

    def detail_payload(self, **kw):
        return {"unit": "EA", "price": "1.00", **kw}

    def detail_api(self, md_id, payload, detail_id=None):
        if detail_id is None:
            return self.call(router_module.md_detail_create, md_id, payload, self.req(), self.db)
        return self.call(router_module.md_detail_update, md_id, detail_id, payload, self.req(), self.db)

    # ---- COLOR in Common Maintenance (one table) ----
    def test_color_crud_lives_only_in_tb_common_code(self):
        self.assertFalse(inspect(self.engine).has_table("tb_color"))
        status, body = self.common("create", {"name": "Teal", "value": "#008080", "display_order": 140, "notes": "new"})
        self.assertEqual((status, body["item"]["code"], body["item"]["value"]), (201, "CLR00014", "#008080"))
        self.assertNotIn("legacy_id", body["item"])
        teal = body["item"]
        status, body = self.common("update", teal["id"], self.form(teal, name="Deep Teal", active=False))
        self.assertEqual((status, body["item"]["name"], body["item"]["active"]), (200, "Deep Teal", False))
        self.assertEqual(self.common("create", {"name": "deep teal"})[1]["errors"]["name"], "This name already exists in this group.")
        self.assertEqual(self.common("delete", teal["id"])[0], 200)
        self.assertEqual(self.common("create", {"name": "Olive"})[1]["item"]["code"], "CLR00014")  # next = highest + 1 (unchanged rule)

    def test_in_use_color_cannot_be_deleted_but_can_be_deactivated(self):
        item = self.md()
        default = next(d for d in svc.list_md_details(self.db, item["id"]) if d["is_default"])
        black = self.color("CLR00001")
        svc.update_md_detail(self.db, item["id"], default["id"], self.detail_payload(color_code_id=black["id"], is_default=True), 1)
        self.assertTrue(self.color("CLR00001")["in_use"])
        before = [tuple(r) for r in self.db.execute(text("SELECT * FROM tb_common_code ORDER BY 1"))]
        status, body = self.common("delete", black["id"])
        self.assertEqual((status, body["detail"]), (409, "This record is in use and cannot be deleted. Deactivate it instead."))
        self.assertEqual([tuple(r) for r in self.db.execute(text("SELECT * FROM tb_common_code ORDER BY 1"))], before)
        self.assertEqual(self.common("update", black["id"], self.form(black, active=False))[0], 200)
        self.assertEqual(next(d for d in svc.list_md_details(self.db, item["id"]) if d["is_default"])["color_code_id"], black["id"])

    # ---- earlier Color API / page ----
    def test_color_api_is_gone_and_page_redirects(self):
        for status, _body in (
                self.call(router_module.master_group_list, "color", self.req(), db=self.db),
                self.call(router_module.master_group_create, "color", {"name": "Teal"}, self.req(), self.db),
                self.call(router_module.master_group_update, "color", 1, {"name": "Jet"}, self.req(), self.db),
                self.call(router_module.master_group_delete, "color", 1, self.req(), self.db)):
            self.assertEqual(status, 404)
        self.assertNotIn("colors", svc.lookup_options(self.db))
        user = SimpleNamespace(i_platform_user_id=1, login_role=1)
        with patch.object(router_module, "_auth_user_or_home_redirect", return_value=(user, None)), \
                patch.object(router_module, "require_platform_admin", return_value=None):
            for query in ("", "group=UNIT&x=1"):
                response = router_module.master_group_page("color", self.req("/platform/master-group/color", query), self.db)
                self.assertEqual((response.status_code, response.headers["location"]),
                                 (307, "/platform/master-group/common-maintenance?group=COLOR"))

    # ---- MD Detail: color_code_id only ----
    def test_md_detail_uses_color_code_id_and_refuses_color_id(self):
        item = self.md()
        navy, white = self.color("CLR00004"), self.color("CLR00002")
        status, body = self.detail_api(item["id"], self.detail_payload(color_code_id=navy["id"], size="M"))
        self.assertEqual((status, body["item"]["color_code_id"]), (201, navy["id"]))
        self.assertNotIn("color_id", body["item"])
        detail_id = body["item"]["id"]
        before = [tuple(r) for r in self.db.execute(text("SELECT * FROM tb_md_detail ORDER BY 1"))]
        for payload in ({"color_id": navy["id"]}, {"color_id": None}, {"color_id": ""},
                        {"color_id": white["id"], "color_code_id": white["id"]}):
            for existing in (None, detail_id):  # create and update
                with self.subTest(payload=payload, update=existing is not None):
                    status, body = self.detail_api(item["id"], self.detail_payload(**payload), existing)
                    self.assertEqual(status, 422)
                    self.assertEqual(body["errors"]["color_id"], svc.COLOR_ID_REMOVED)
        self.assertEqual([tuple(r) for r in self.db.execute(text("SELECT * FROM tb_md_detail ORDER BY 1"))], before)
        for bad, message in ((1000, "Color does not exist."), (self.db.query(CommonCode.i_common_code_id).filter(
                CommonCode.c_code == "EA").scalar(), "Color does not exist.")):  # a UNIT code is not a Color
            with self.subTest(bad=bad):
                status, body = self.detail_api(item["id"], self.detail_payload(color_code_id=bad), detail_id)
                self.assertEqual((status, body["errors"]), (422, {"color_code_id": message}))
        # Use Color OFF: the Store payload loses color_code_id (saved value kept); color_id is still refused.
        off = {"color": False}
        kept = svc.update_md_detail(self.db, item["id"], detail_id,
                                    without_disabled_item_features(self.detail_payload(color_code_id=white["id"], size="S"), off), 1)
        self.assertEqual((kept["color_code_id"], kept["size"]), (navy["id"], "S"))
        status, body = self.detail_api(item["id"], without_disabled_item_features(self.detail_payload(color_id=1), off), detail_id)
        self.assertEqual(status, 422)
        cleared = svc.update_md_detail(self.db, item["id"], detail_id, self.detail_payload(color_code_id=""), 1)
        self.assertIsNone(cleared["color_code_id"])
        options = svc.lookup_options(self.db)["color_codes"]
        self.assertEqual([(c["code"], c["name"]) for c in options][:2], [("CLR00001", "Black"), ("CLR00002", "White")])
        self.assertNotIn("legacy_id", options[0])

    def test_inactive_color_kept_when_saved_and_refused_when_new(self):
        item = self.md()
        black = self.color("CLR00001")
        detail = svc.create_md_detail(self.db, item["id"], self.detail_payload(color_code_id=black["id"]), 1)
        self.assertEqual(self.common("update", black["id"], self.form(black, active=False))[0], 200)
        resaved = svc.update_md_detail(self.db, item["id"], detail["id"],
                                       self.detail_payload(color_code_id=black["id"], size="XL"), 1)
        self.assertEqual((resaved["color_code_id"], resaved["size"]), (black["id"], "XL"))
        other = svc.create_md_detail(self.db, item["id"], self.detail_payload(color_code_id=self.color("CLR00002")["id"]), 1)
        with self.assertRaises(svc.FieldErrors) as ctx:
            svc.update_md_detail(self.db, item["id"], other["id"], self.detail_payload(color_code_id=black["id"]), 1)
        self.assertEqual(ctx.exception.errors, {"color_code_id": "Color is inactive."})


if __name__ == "__main__":
    unittest.main()
