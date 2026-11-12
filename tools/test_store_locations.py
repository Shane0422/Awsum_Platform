"""Location Management: DB constraints, Location Master CRUD (Master Group common), Store scope and MD Detail links."""
import unittest

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.pg_platform import PlatformBase
from backend.models_admin.store_location import StoreCategory3Location, StoreLocation, StoreMdLocation


def sqlite_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):  # SQLite enforces foreign keys only when asked
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
    return engine


class StoreLocationSchemaTests(unittest.TestCase):
    def setUp(self):
        self.engine = sqlite_engine()
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as conn:  # minimal parents; the real tables come from the platform models
            conn.execute(text("CREATE TABLE tb_store (i_store_id INTEGER PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE tb_md_detail (i_md_detail_id INTEGER PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE tb_category3 (i_category3_id INTEGER PRIMARY KEY)"))
            conn.execute(text("INSERT INTO tb_store VALUES (1), (2)"))
            conn.execute(text("INSERT INTO tb_md_detail VALUES (10), (11)"))
            conn.execute(text("INSERT INTO tb_category3 VALUES (30)"))
        PlatformBase.metadata.create_all(self.engine, tables=[StoreLocation.__table__, StoreMdLocation.__table__,
                                                              StoreCategory3Location.__table__])
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.db.close)

    def add(self, row):
        self.db.add(row)
        self.db.commit()
        return row

    def rejected(self, row):
        self.db.add(row)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_code_unique_per_store_ignoring_case_and_other_store_may_reuse(self):
        self.add(StoreLocation(i_store_id=1, c_location_code="A-01", c_location_name="Aisle 1"))
        self.rejected(StoreLocation(i_store_id=1, c_location_code="a-01", c_location_name="Dup"))
        self.add(StoreLocation(i_store_id=2, c_location_code="A-01", c_location_name="Other Store"))

    def test_link_cannot_point_at_another_stores_location_and_primary_is_at_most_one(self):
        a = self.add(StoreLocation(i_store_id=1, c_location_code="A-01", c_location_name="A"))
        b = self.add(StoreLocation(i_store_id=1, c_location_code="B-01", c_location_name="B"))
        other = self.add(StoreLocation(i_store_id=2, c_location_code="Z-01", c_location_name="Z"))
        self.rejected(StoreMdLocation(i_store_id=1, i_md_detail_id=10, i_location_id=other.i_location_id))
        self.add(StoreMdLocation(i_store_id=1, i_md_detail_id=10, i_location_id=a.i_location_id, b_is_primary=True))
        self.rejected(StoreMdLocation(i_store_id=1, i_md_detail_id=10, i_location_id=a.i_location_id))  # same pair twice
        self.rejected(StoreMdLocation(i_store_id=1, i_md_detail_id=10, i_location_id=b.i_location_id, b_is_primary=True))
        self.add(StoreMdLocation(i_store_id=1, i_md_detail_id=10, i_location_id=b.i_location_id, b_is_primary=False))
        # Another Store keeps its own Primary for the same detail.
        self.add(StoreMdLocation(i_store_id=2, i_md_detail_id=10, i_location_id=other.i_location_id, b_is_primary=True))

    def test_deleting_a_detail_removes_only_its_links_and_used_location_is_protected(self):
        a = self.add(StoreLocation(i_store_id=1, c_location_code="A-01", c_location_name="A"))
        self.add(StoreMdLocation(i_store_id=1, i_md_detail_id=10, i_location_id=a.i_location_id, b_is_primary=True))
        self.add(StoreMdLocation(i_store_id=1, i_md_detail_id=11, i_location_id=a.i_location_id))
        with self.engine.begin() as conn:
            with self.assertRaises(IntegrityError):  # a linked location cannot be deleted
                with conn.begin_nested():
                    conn.execute(text("DELETE FROM tb_store_location WHERE i_location_id = :id"), {"id": a.i_location_id})
            conn.execute(text("DELETE FROM tb_md_detail WHERE i_md_detail_id = 10"))
        self.db.expire_all()
        self.assertEqual([r.i_md_detail_id for r in self.db.query(StoreMdLocation)], [11])
        self.assertEqual(self.db.query(StoreLocation).count(), 1)

    def test_links_follow_the_area_and_the_table_has_no_type_or_parent(self):
        w01 = self.add(StoreLocation(i_store_id=1, c_location_code="W01", c_location_name="W", c_location_area="WAREHOUSE"))
        r02 = self.add(StoreLocation(i_store_id=1, c_location_code="R02", c_location_name="R", c_location_area="WAREHOUSE"))
        a03 = self.add(StoreLocation(i_store_id=1, c_location_code="A03", c_location_name="A"))
        self.assertEqual(a03.c_location_area, "SALES_FLOOR")  # default area
        columns = set(StoreLocation.__table__.columns.keys())
        self.assertFalse({"i_parent_location_id", "c_location_type"} & columns)  # flat list per Store and Area
        self.assertNotIn("uq_tb_store_location_id_store", {c.name for c in StoreLocation.__table__.constraints})
        # Links carry the location's area: a mismatching area (or another Store) is refused.
        self.rejected(StoreMdLocation(i_store_id=1, i_md_detail_id=10, i_location_id=r02.i_location_id, c_location_area="SALES_FLOOR"))
        # One Primary per Store + Detail + Area: a Sales Floor and a Warehouse Primary together are fine.
        self.add(StoreMdLocation(i_store_id=1, i_md_detail_id=10, i_location_id=a03.i_location_id, b_is_primary=True))
        self.add(StoreMdLocation(i_store_id=1, i_md_detail_id=10, i_location_id=r02.i_location_id,
                                 c_location_area="WAREHOUSE", b_is_primary=True))
        self.rejected(StoreMdLocation(i_store_id=1, i_md_detail_id=10, i_location_id=w01.i_location_id,
                                      c_location_area="WAREHOUSE", b_is_primary=True))

    def test_migration_adds_columns_to_existing_tables_keeps_rows_and_is_repeatable(self):
        from backend.database.store_location_migration import migrate_store_locations
        engine = sqlite_engine()
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:  # tables as created by the earlier Shelf Location release
            conn.execute(text("CREATE TABLE tb_store_location (i_location_id INTEGER PRIMARY KEY, i_store_id INTEGER NOT NULL, "
                              "c_location_code VARCHAR(30) NOT NULL, c_location_name VARCHAR(100) NOT NULL)"))
            conn.execute(text("CREATE TABLE tb_store_md_location (i_store_md_location_id INTEGER PRIMARY KEY, i_store_id INTEGER, "
                              "i_md_detail_id INTEGER, i_location_id INTEGER, b_is_primary BOOLEAN)"))
            conn.execute(text("CREATE UNIQUE INDEX uq_tb_store_md_location_primary ON tb_store_md_location (i_store_id, i_md_detail_id) "
                              "WHERE b_is_primary = 1"))
            conn.execute(text("INSERT INTO tb_store_location VALUES (1, 1, 'A-01', 'Aisle')"))
            conn.execute(text("INSERT INTO tb_store_md_location VALUES (1, 1, 10, 1, 1)"))
        migrate_store_locations(engine)
        migrate_store_locations(engine)  # rerun changes nothing
        from sqlalchemy import inspect as sa_inspect
        insp = sa_inspect(engine)
        location_columns = {c["name"] for c in insp.get_columns("tb_store_location")}
        self.assertIn("c_location_area", location_columns)
        self.assertNotIn("i_parent_location_id", location_columns)  # the parent tree is no longer added
        self.assertIn("c_location_area", {c["name"] for c in insp.get_columns("tb_store_md_location")})
        self.assertEqual({i["name"] for i in insp.get_indexes("tb_store_md_location")}, {"uq_tb_store_md_location_area_primary"})
        with engine.connect() as conn:
            self.assertEqual(tuple(conn.execute(text("SELECT i_location_id, c_location_code, c_location_area "
                                                     "FROM tb_store_location")).one()), (1, "A-01", "SALES_FLOOR"))
            self.assertEqual(tuple(conn.execute(text("SELECT i_md_detail_id, b_is_primary, c_location_area FROM tb_store_md_location")).one()),
                             (10, 1, "SALES_FLOOR"))


class ShelfBase(unittest.TestCase):
    """Full platform schema on SQLite (foreign keys on) with one Merchant and two Stores."""

    def setUp(self):
        import backend.models_admin  # noqa: F401  (every platform table)
        from backend.models_admin.merchant import Merchant
        from backend.models_admin.store import Store
        self.engine = sqlite_engine()
        self.addCleanup(self.engine.dispose)
        PlatformBase.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.db.close)
        self.db.add(Merchant(i_merchant_id=1, c_merchant_code="MER1", c_merchant_name="M1", c_status="active"))
        self.db.add_all([Store(i_store_id=1, i_merchant_id=1, c_store_code="ST1", c_store_name="One", c_status="active"),
                         Store(i_store_id=2, i_merchant_id=1, c_store_code="ST2", c_store_name="Two", c_status="active")])
        self.db.commit()

    def create(self, store_id=1, **kw):
        from backend.services import master_group as svc
        return svc.create_item(self.db, "location", {"code": "a-01", "name": "Aisle 1", **kw}, 7, store_id=store_id)

    def errors(self, fn):
        from backend.services import master_group as svc
        with self.assertRaises(svc.FieldErrors) as ctx:
            fn()
        self.db.rollback()
        return ctx.exception.errors



class ShelfMasterServiceTests(ShelfBase):
    """Shelf Master through the Master Group common CRUD with a Store scope."""

    def test_crud_code_rules_and_store_isolation(self):
        from backend.services import master_group as svc
        item = self.create(zone="Z1", aisle="3", bay="B", level="2", display_order=5)
        self.assertEqual((item["code"], item["store_id"], item["location_area"]), ("A-01", 1, "SALES_FLOOR"))
        self.assertEqual((item["zone"], item["aisle"], item["bay"], item["level"], item["display_order"]), ("Z1", "3", "B", "2", 5))
        b01 = self.create(code="B-01")
        self.assertFalse({"location_type", "location_type_name", "parent_id", "parent_code", "path", "depth", "tree_key",
                          "active_children"} & set(b01))  # no Type / Parent / Path any more
        row = self.db.get(StoreLocation, item["id"])
        self.assertEqual((row.i_created_by, row.i_updated_by), (7, 7))  # common audit
        # Code: required, upper-cased, unique per Store ignoring case; another Store may reuse it.
        self.assertEqual(self.errors(lambda: self.create(code=" a-01 "))["code"], "Location Code A-01 already exists in this Store.")
        for bad in ("", "A 01", "#1", "x" * 31):
            with self.subTest(bad):
                self.assertIn("code", self.errors(lambda: self.create(code=bad)))
        self.assertIn("location_area", self.errors(lambda: self.create(code="C-01", location_area="ROOF")))
        self.assertIn("name", self.errors(lambda: self.create(code="C-02", name="x" * 101)))
        self.assertEqual(self.create(store_id=2)["code"], "A-01")
        # Each Store sees only its own rows; another Store's id is "not found" (list, update, delete, export).
        self.assertEqual([r["code"] for r in svc.list_items(self.db, "location", store_id=1, sort="code")["items"]], ["A-01", "B-01"])
        self.assertEqual(svc.list_items(self.db, "location", store_id=2)["total"], 1)
        for action in (lambda: svc.update_item(self.db, "location", item["id"], {"code": "X", "name": "X"}, 1, store_id=2),
                       lambda: svc.delete_item(self.db, "location", item["id"], store_id=2)):
            with self.assertRaises(svc.NotFound):
                action()
            self.db.rollback()
        with self.assertRaises(svc.NotFound):
            svc.list_items(self.db, "location")  # no Store selected
        # Update keeps the Store and the code: it is fixed after create (Category III / MD Master keep codes as text).
        updated = svc.update_item(self.db, "location", item["id"], {"name": "Aisle 2", "store_id": 2}, 1, store_id=1)
        self.assertEqual((updated["code"], updated["name"], updated["store_id"]), ("A-01", "Aisle 2", 1))
        self.assertEqual(svc.update_item(self.db, "location", item["id"], {"code": " a-01 ", "name": "Aisle 2"}, 1, store_id=1)["code"],
                         "A-01")  # the same code (any case / spaces) is accepted
        for changed in ("a-02", "b-01", ""):
            with self.subTest(changed=changed):
                self.assertEqual(self.errors(lambda: svc.update_item(self.db, "location", item["id"], {"code": changed, "name": "x"},
                                                                     1, store_id=1))["code"], svc.LOCATION_CODE_FIXED)
        self.assertEqual(self.db.get(StoreLocation, item["id"]).c_location_code, "A-01")
        headers, data = svc.export_table(self.db, "location", store_id=1, sort="code")
        self.assertEqual(headers, ["Location Code", "Location Name", "Area", "Display Order", "Status", "Notes"])
        self.assertEqual([line[:3] for line in data], [["A-01", "Aisle 2", "Sales Floor"], ["B-01", "Aisle 1", "Sales Floor"]])
        self.assertEqual([t["code"] for t in svc.lookup_options(self.db)["location_areas"]], ["SALES_FLOOR", "WAREHOUSE"])
        self.assertNotIn("location_types", svc.lookup_options(self.db))
        # Unused: deletable.
        svc.delete_item(self.db, "location", updated["id"], store_id=1)
        self.assertEqual(svc.list_items(self.db, "location", store_id=1)["total"], 1)


class LocationFlatListTests(ShelfBase):
    """Location Code + Sales Floor / Warehouse only: no Location Type and no parent tree."""

    def test_name_defaults_to_the_code_and_type_parent_keys_are_ignored(self):
        from backend.services import master_group as svc
        made = self.create(code="w01-b02-c01", name="  ", location_area="warehouse", location_type="RACK", parent_id=99999)
        self.assertEqual((made["code"], made["name"], made["location_area"], made["location_area_name"]),
                         ("W01-B02-C01", "W01-B02-C01", "WAREHOUSE", "Warehouse"))
        self.assertEqual(self.create(code="A01", name=None)["name"], "A01")  # absent / null name -> the code
        self.assertEqual(self.create(code="A02", name="Front aisle")["name"], "Front aisle")
        row = self.db.get(StoreLocation, made["id"])
        self.assertEqual((row.c_location_name, row.c_location_area), ("W01-B02-C01", "WAREHOUSE"))
        # Edit: a cleared name becomes the (fixed) code again; the Area is kept when not sent.
        named = svc.update_item(self.db, "location", made["id"], {"name": "Cold rack"}, 1, store_id=1)
        self.assertEqual((named["name"], named["location_area"]), ("Cold rack", "WAREHOUSE"))
        cleared = svc.update_item(self.db, "location", made["id"], {"name": ""}, 1, store_id=1)
        self.assertEqual((cleared["code"], cleared["name"]), ("W01-B02-C01", "W01-B02-C01"))
        # Area: Sales Floor / Warehouse only; an unused location may change it.
        self.assertIn("location_area", self.errors(lambda: svc.update_item(
            self.db, "location", made["id"], {"location_area": "ROOF"}, 1, store_id=1)))
        self.assertEqual(svc.update_item(self.db, "location", made["id"], {"location_area": "SALES_FLOOR"}, 1,
                                         store_id=1)["location_area"], "SALES_FLOOR")

    def test_deactivate_activate_delete_without_any_hierarchy_rule(self):
        from backend.services import master_group as svc
        a03 = self.create(code="A03-B01-C01", location_area="SALES_FLOOR")
        other = self.create(code="A03-B01-C01", store_id=2)  # same code in another Store
        off = svc.update_item(self.db, "location", a03["id"], {"active": False}, 1, store_id=1)
        self.assertEqual((off["active"], off["in_use"]), (False, False))
        self.assertTrue(svc.update_item(self.db, "location", a03["id"], {"active": True}, 1, store_id=1)["active"])
        svc.delete_item(self.db, "location", a03["id"], store_id=1)
        self.assertIsNone(self.db.get(StoreLocation, a03["id"]))
        self.assertEqual((self.db.get(StoreLocation, other["id"]).b_active, self.db.get(StoreLocation, other["id"]).c_location_code),
                         (True, "A03-B01-C01"))  # another Store's same code is untouched

    def test_used_location_keeps_its_area_and_cannot_be_deleted(self):
        from backend.services import master_group as svc
        w = self.create(code="W01", location_area="WAREHOUSE")
        # A link (deprecated table) marks it in use: it cannot change Area or be deleted (checked through _location_referenced).
        original = svc._location_referenced
        svc._location_referenced = lambda db, location_id: location_id == w["id"]
        self.addCleanup(setattr, svc, "_location_referenced", original)
        self.assertTrue(svc.list_items(self.db, "location", store_id=1)["items"][0]["in_use"])
        self.assertIn("in use", self.errors(lambda: svc.update_item(
            self.db, "location", w["id"], {"location_area": "SALES_FLOOR"}, 1, store_id=1))["location_area"])
        with self.assertRaises(svc.Conflict):
            svc.delete_item(self.db, "location", w["id"], store_id=1)
        self.db.rollback()

    def test_lookup_sales_floor_first_then_code(self):
        from backend.services import store_locations as links
        for code, area in (("W02", "WAREHOUSE"), ("A02", "SALES_FLOOR"), ("W01", "WAREHOUSE"), ("A01", "SALES_FLOOR")):
            self.create(code=code, location_area=area)
        items = links.store_location_lookup(self.db, 1)
        self.assertEqual([i["code"] for i in items], ["A01", "A02", "W01", "W02"])
        self.assertEqual(set(items[0]), {"id", "code", "name", "location_area", "location_area_name", "active"})
        self.assertEqual(links.store_location_lookup(self.db, 2), [])


class LocationHierarchyRemovalTests(unittest.TestCase):
    """drop_location_hierarchy(): the earlier Type / parent columns are archived and dropped; every row stays."""

    def test_drop_archives_parent_and_type_and_keeps_rows(self):
        import json
        import tempfile
        from sqlalchemy import inspect as sa_inspect
        from backend.database.store_location_migration import drop_location_hierarchy, legacy_location_targets
        engine = sqlite_engine()
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:  # the earlier shape (Type + parent tree)
            conn.execute(text("CREATE TABLE tb_store_location (i_location_id INTEGER PRIMARY KEY, i_store_id INTEGER NOT NULL, "
                              "c_location_code VARCHAR(30) NOT NULL, c_location_name VARCHAR(100) NOT NULL, "
                              "c_location_area VARCHAR(20) NOT NULL DEFAULT 'SALES_FLOOR', i_parent_location_id INTEGER, "
                              "c_location_type VARCHAR(20) NOT NULL DEFAULT 'SHELF', c_zone VARCHAR(50), c_aisle VARCHAR(50), "
                              "c_bay VARCHAR(50), c_level VARCHAR(50), i_display_order INTEGER NOT NULL DEFAULT 100, "
                              "b_active BOOLEAN NOT NULL DEFAULT 1, c_notes TEXT, dt_created TIMESTAMP, i_created_by INTEGER, "
                              "dt_updated TIMESTAMP, i_updated_by INTEGER)"))
            conn.execute(text("CREATE INDEX ix_tb_store_location_i_parent_location_id ON tb_store_location (i_parent_location_id)"))
            conn.execute(text("INSERT INTO tb_store_location (i_location_id, i_store_id, c_location_code, c_location_name, "
                              "c_location_area, i_parent_location_id, c_location_type, c_notes) VALUES "
                              "(1, 1, 'A03', 'Aisle 3', 'SALES_FLOOR', NULL, 'AISLE', 'n'), "
                              "(2, 1, 'B02', 'Bay 2', 'SALES_FLOOR', 1, 'BAY', NULL), (3, 2, 'A03', 'Other', 'WAREHOUSE', NULL, 'RACK', NULL)"))
        with engine.connect() as conn:
            self.assertEqual(legacy_location_targets(conn), ["column tb_store_location.i_parent_location_id",
                                                             "column tb_store_location.c_location_type"])
            before = [tuple(r) for r in conn.execute(text("SELECT i_location_id, i_store_id, c_location_code, c_location_name, "
                                                          "c_location_area, c_notes FROM tb_store_location ORDER BY 1"))]
        with tempfile.TemporaryDirectory() as folder:
            result = drop_location_hierarchy(engine, folder)
            archive = json.loads(open(result["archive"], encoding="utf-8").read())
        self.assertEqual(result["rows"], 3)
        self.assertEqual([(r["c_location_code"], r["parent_code"], r["c_location_type"]) for r in archive["rows"]],
                         [("A03", None, "AISLE"), ("B02", "A03", "BAY"), ("A03", None, "RACK")])
        columns = {c["name"] for c in sa_inspect(engine).get_columns("tb_store_location")}
        self.assertFalse({"i_parent_location_id", "c_location_type"} & columns)
        with engine.connect() as conn:
            self.assertEqual(legacy_location_targets(conn), [])
            self.assertEqual([tuple(r) for r in conn.execute(text("SELECT i_location_id, i_store_id, c_location_code, c_location_name, "
                                                                  "c_location_area, c_notes FROM tb_store_location ORDER BY 1"))], before)
        self.assertEqual(drop_location_hierarchy(engine, "unused"), {"dropped": [], "archive": None})  # rerun: nothing


class LocationRangeTests(ShelfBase):
    """Range form: Zone + two From/To ranges -> every combination; existing codes skipped; one transaction."""

    SALES = {"location_area": "SALES_FLOOR", "zone": "s01", "bay_from": 1, "bay_to": 1, "shelf_from": 1, "shelf_to": 1}
    WAREHOUSE = {"location_area": "WAREHOUSE", "zone": "W01", "rack_from": 1, "rack_to": 1, "level_from": 1, "level_to": 1}

    def test_codes_one_or_every_combination_in_code_order(self):
        from backend.services import master_group as svc
        self.assertEqual(svc.location_range_codes(self.SALES), ("SALES_FLOOR", ["S01-B01-C01"]))  # From = To: one code
        self.assertEqual(svc.location_range_codes({**self.SALES, "bay_to": 2, "shelf_from": "9", "shelf_to": "10"})[1],
                         ["S01-B01-C09", "S01-B01-C10", "S01-B02-C09", "S01-B02-C10"])
        self.assertEqual(svc.location_range_codes({**self.WAREHOUSE, "location_area": "warehouse", "rack_from": 3, "rack_to": 4,
                                                   "level_to": 2}),
                         ("WAREHOUSE", ["W01-R03-C01", "W01-R03-C02", "W01-R04-C01", "W01-R04-C02"]))
        # The other Area's keys are ignored (Warehouse uses Rack / Level only).
        self.assertEqual(svc.location_range_codes({**self.WAREHOUSE, "bay_from": 5})[1], ["W01-R01-C01"])

    def test_validation_messages_use_the_form_fields(self):
        from backend.services import master_group as svc
        cases = (
            ({**self.SALES, "location_area": "ROOF"}, "location_area"),
            ({**self.SALES, "location_area": ""}, "location_area"),
            ({**self.SALES, "zone": ""}, "zone"),
            ({**self.SALES, "zone": "S-01"}, "zone"),
            ({**self.SALES, "bay_from": ""}, "bay_from"),
            ({**self.SALES, "shelf_to": 0}, "shelf_to"),
            ({**self.SALES, "shelf_to": 100}, "shelf_to"),
            ({**self.SALES, "bay_from": 1.5}, "bay_from"),
            ({**self.SALES, "bay_from": 3, "bay_to": 2}, "bay_to"),
            ({**self.WAREHOUSE, "level_to": None}, "level_to"),
            ({**self.SALES, "bay_to": 26, "shelf_to": 20}, "bay_to"),  # 520 codes: over the 500 limit
        )
        for payload, key in cases:
            with self.subTest(payload=payload):
                self.assertIn(key, self.errors(lambda: svc.location_range_codes(payload)))
        self.assertEqual(len(svc.location_range_codes({**self.SALES, "bay_to": 25, "shelf_to": 20})[1]), 500)

    def test_preview_counts_existing_codes_of_this_store_only(self):
        from backend.services import master_group as svc
        self.create(code="s01-b01-c02", location_area="SALES_FLOOR")        # existing (any case)
        self.create(code="S01-B01-C03", store_id=2)                          # another Store: not a duplicate
        preview = svc.preview_location_range(self.db, {**self.SALES, "shelf_to": 3}, store_id=1)
        self.assertEqual((preview["total"], preview["duplicates"], preview["new"]), (3, 1, 2))
        self.assertEqual([(c["code"], c["exists"]) for c in preview["codes"]],
                         [("S01-B01-C01", False), ("S01-B01-C02", True), ("S01-B01-C03", False)])
        with self.assertRaises(svc.NotFound):
            svc.preview_location_range(self.db, self.SALES)  # no Store selected

    def test_create_saves_only_new_codes_with_name_default_and_common_fields(self):
        from backend.services import master_group as svc
        self.create(code="S01-B01-C02", name="Kept", location_area="SALES_FLOOR")
        result = svc.create_location_range(self.db, {**self.SALES, "shelf_to": 3, "display_order": 7, "active": False,
                                                     "notes": "range"}, 9, store_id=1)
        self.assertEqual((result["total"], result["created"], result["duplicates"], result["skipped"]), (3, 2, 1, ["S01-B01-C02"]))
        rows = {r.c_location_code: r for r in self.db.query(StoreLocation).filter(StoreLocation.i_store_id == 1)}
        self.assertEqual(sorted(rows), ["S01-B01-C01", "S01-B01-C02", "S01-B01-C03"])
        new = rows["S01-B01-C03"]
        self.assertEqual((new.c_location_name, new.c_location_area, new.i_display_order, new.b_active, new.c_notes,
                          new.i_created_by), ("S01-B01-C03", "SALES_FLOOR", 7, False, "range", 9))
        self.assertEqual(rows["S01-B01-C02"].c_location_name, "Kept")  # the existing row is untouched
        named = svc.create_location_range(self.db, {**self.WAREHOUSE, "level_to": 2, "name": "Back rack"}, 9, store_id=1)
        self.assertEqual([(i["code"], i["name"], i["location_area"]) for i in named["items"]],
                         [("W01-R01-C01", "Back rack", "WAREHOUSE"), ("W01-R01-C02", "Back rack", "WAREHOUSE")])
        again = svc.create_location_range(self.db, {**self.WAREHOUSE, "level_to": 2}, 9, store_id=1)
        self.assertEqual((again["created"], again["duplicates"], again["items"]), (0, 2, []))  # nothing new: nothing saved
        self.assertEqual(svc.create_location_range(self.db, self.SALES, 9, store_id=2)["created"], 1)  # other Store may reuse

    def test_concurrent_duplicate_rolls_back_the_whole_range(self):
        from backend.services import master_group as svc
        self.create(code="S01-B01-C02", location_area="SALES_FLOOR")
        before = self.db.query(StoreLocation).count()
        original = svc._existing_location_codes
        svc._existing_location_codes = lambda db, store_id, codes: set()  # as if another save added C02 after the check
        self.addCleanup(setattr, svc, "_existing_location_codes", original)
        with self.assertRaises(svc.Conflict):
            svc.create_location_range(self.db, {**self.SALES, "shelf_to": 3}, 9, store_id=1)
        self.assertEqual(self.db.query(StoreLocation).count(), before)  # the DB unique index stopped it; C01 / C03 not saved


class DefaultLocationInitTests(ShelfBase):
    """First visit of a Store: S01-B01..B10-C01 and W01-R01..R10-C01 once; recorded per Store; one transaction."""

    SALES = [f"S01-B{n:02d}-C01" for n in range(1, 11)]
    WAREHOUSE = [f"W01-R{n:02d}-C01" for n in range(1, 11)]

    def init(self, store_id=1, user_id=7):
        from backend.services import master_group as svc
        return svc.initialize_store_locations(self.db, user_id, store_id=store_id)

    def codes(self, store_id=1, area=None):
        from backend.services import master_group as svc
        return [i["code"] for i in svc.list_items(self.db, "location", store_id=store_id, sort="code", size=100,
                                                   location_area=area)["items"]]

    def record(self, store_id=1):
        from backend.models_admin.store_location import StoreLocationInit
        return self.db.get(StoreLocationInit, store_id)

    def test_zero_locations_creates_twenty_defaults_shown_in_both_columns(self):
        self.assertIsNone(self.record())
        self.assertEqual(self.init(), {"initialized": True, "created": 20, "reason": "created"})
        self.assertEqual(self.codes(area="SALES_FLOOR"), self.SALES)        # Sales Floor column
        self.assertEqual(self.codes(area="WAREHOUSE"), self.WAREHOUSE)      # Warehouse column
        row = self.db.query(StoreLocation).filter(StoreLocation.c_location_code == "W01-R10-C01").one()
        self.assertEqual((row.c_location_name, row.c_location_area, row.b_active, row.i_display_order, row.i_created_by),
                         ("W01-R10-C01", "WAREHOUSE", True, 100, 7))
        self.assertEqual((self.record().i_created_count, self.record().i_created_by), (20, 7))
        self.assertEqual(self.init(), {"initialized": False, "created": 0, "reason": "already_initialized"})
        self.assertEqual(len(self.codes()), 20)  # a second visit adds nothing

    def test_any_existing_location_skips_generation_but_records_the_store(self):
        self.create(code="W99", location_area="WAREHOUSE")  # one Warehouse location only: Areas are not judged apart
        self.assertEqual(self.init(), {"initialized": True, "created": 0, "reason": "existing_locations"})
        self.assertEqual(self.codes(), ["W99"])
        self.assertEqual(self.record().i_created_count, 0)

    def test_deleting_every_location_after_initialization_never_recreates(self):
        from backend.services import master_group as svc
        self.init()
        for row in list(self.db.query(StoreLocation).filter(StoreLocation.i_store_id == 1)):
            svc.delete_item(self.db, "location", row.i_location_id, store_id=1)
        self.assertEqual(self.codes(), [])
        self.assertEqual(self.init()["created"], 0)
        self.assertEqual(self.codes(), [])                                  # stays empty (the guide shows)
        self.assertIsNotNone(self.record())                                 # the record outlives its Locations

    def test_store_isolation(self):
        self.create(code="S01-B01-C01", store_id=2)                         # Store 2 already has data
        self.assertEqual(self.init(1)["created"], 20)
        self.assertEqual(self.codes(2), ["S01-B01-C01"])                    # untouched by Store 1
        self.assertIsNone(self.record(2))
        self.assertEqual(self.init(2)["created"], 0)                        # its own decision: it has a Location
        self.assertEqual(len(self.codes(1)), 20)
        from backend.services import master_group as svc
        with self.assertRaises(svc.NotFound):
            svc.initialize_store_locations(self.db, 7)                      # no Store selected

    def test_failure_rolls_back_the_record_and_every_location(self):
        from backend.services import master_group as svc
        original = svc.location_range_codes
        calls = []

        def broken(spec):  # the Warehouse codes collide with each other -> the unique index refuses the whole save
            calls.append(spec)
            area, codes = original(spec)
            return area, (codes if area == "SALES_FLOOR" else codes[:1] * 2)
        svc.location_range_codes = broken
        self.addCleanup(setattr, svc, "location_range_codes", original)
        self.assertEqual(self.init()["reason"], "concurrent")
        self.assertEqual((self.codes(), self.record()), ([], None))         # nothing half-saved


class DefaultLocationConcurrencyTests(unittest.TestCase):
    """Concurrent first visits (separate connections on one file database): the defaults are created exactly once."""

    def setUp(self):
        import os
        import tempfile
        import backend.models_admin  # noqa: F401
        from backend.models_admin.merchant import Merchant
        from backend.models_admin.store import Store
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        self.engine = create_engine(f"sqlite:///{os.path.join(folder, 'concurrent.db')}", connect_args={"timeout": 30})
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def _fk_on(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
        PlatformBase.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add(Merchant(i_merchant_id=1, c_merchant_code="MER1", c_merchant_name="M1", c_status="active"))
            db.add_all([Store(i_store_id=1, i_merchant_id=1, c_store_code="ST1", c_store_name="One", c_status="active"),
                        Store(i_store_id=2, i_merchant_id=1, c_store_code="ST2", c_store_name="Two", c_status="active")])
            db.commit()

    def counts(self, store_id):
        from backend.models_admin.store_location import StoreLocationInit
        with self.Session() as db:
            return (db.query(StoreLocation).filter(StoreLocation.i_store_id == store_id).count(),
                    db.query(StoreLocationInit).filter(StoreLocationInit.i_store_id == store_id).count())

    def test_parallel_first_visits_create_once(self):
        import threading
        from backend.services import master_group as svc
        barrier, results, errors = threading.Barrier(6), [], []

        def visit(store_id):
            try:
                with self.Session() as db:
                    barrier.wait()
                    results.append((store_id, svc.initialize_store_locations(db, 7, store_id=store_id)["created"]))
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(repr(exc))
        threads = [threading.Thread(target=visit, args=(1 if n < 4 else 2,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(c for s, c in results if s == 1), [0, 0, 0, 20])  # Store 1: four visits, one creates
        self.assertEqual(sorted(c for s, c in results if s == 2), [0, 20])
        self.assertEqual((self.counts(1), self.counts(2)), ((20, 1), (20, 1)))

    def test_record_committed_by_another_visit_after_the_check_rolls_back(self):
        from backend.models_admin.store_location import StoreLocationInit
        from backend.services import master_group as svc
        original = svc.location_range_codes
        done = []

        def racing(spec):  # another visit finishes its initialization between this visit's checks and its writes
            if not done:
                done.append(True)
                with self.Session() as other:
                    other.add(StoreLocationInit(i_store_id=1, i_created_count=0))
                    other.commit()
            return original(spec)
        svc.location_range_codes = racing
        self.addCleanup(setattr, svc, "location_range_codes", original)
        with self.Session() as db:
            self.assertEqual(svc.initialize_store_locations(db, 7, store_id=1), {"initialized": False, "created": 0,
                                                                                  "reason": "concurrent"})
        self.assertEqual(self.counts(1), (0, 1))  # the other visit's record only; none of this visit's Locations


class RecommendedLocationTests(ShelfBase):
    """Common recommended positions: Category III default (all Stores) and MD Master Override; guidance only."""

    def setUp(self):
        super().setUp()
        from backend.database.item_kind_migration import migrate_item_kinds
        from backend.services import master_group as svc
        migrate_item_kinds(self.engine)
        tax = svc.create_item(self.db, "tax", {"name": "State", "rate": "7"}, 1)
        c1 = svc.create_item(self.db, "category1", {"name": "Food"}, 1)
        c2 = svc.create_item(self.db, "category2", {"name": "Drinks", "category1_id": c1["id"]}, 1)
        self.leaf = {"name": "Soda", "category1_id": c1["id"], "category2_id": c2["id"], "tax1_id": tax["id"],
                     "inventory_type": 2, "keep": "COLD"}
        # New values must be active Location Codes of their Area in some Store (Common Location Selector).
        self.db.add_all([StoreLocation(i_store_id=2, c_location_code=code, c_location_name=code, c_location_area=area)
                         for code, area in (("A03", "SALES_FLOOR"), ("A03-B02-S04", "SALES_FLOOR"), ("B05", "SALES_FLOOR"),
                                            ("X" * 30, "SALES_FLOOR"), ("W01-R02-L03", "WAREHOUSE"))])
        self.db.commit()
        self.c3 = svc.create_item(self.db, "category3", {**self.leaf, "sales_location": " a03 ", "warehouse_location": "w01-r02-l03"}, 1)
        self.cats = {"category1_id": c1["id"], "category2_id": c2["id"], "category3_id": self.c3["id"]}
        self.md_body = {"name": "Cola", "item_kind": "ITEM", **self.cats, "unit": "EA", "retail_price": "1", "billing_cycle": "ONE_TIME"}

    def md(self, **kw):
        from backend.services import master_group as svc
        return svc.create_item(self.db, "md", {**self.md_body, **kw}, 1)

    def test_category3_default_is_common_trimmed_uppercase_and_kept_when_absent(self):
        from backend.services import master_group as svc
        self.assertEqual((self.c3["sales_location"], self.c3["warehouse_location"]), ("A03", "W01-R02-L03"))
        row = self.db.get(svc.Category3, self.c3["id"])
        self.assertEqual((row.c_sales_location, row.c_warehouse_location), ("A03", "W01-R02-L03"))
        # A save without the keys (older client / Store with Location Management OFF) keeps them; blank clears.
        kept = svc.update_item(self.db, "category3", self.c3["id"], {**self.leaf, "name": "Soda 2"}, 1)
        self.assertEqual((kept["sales_location"], kept["tax1_id"], kept["inventory_type"], kept["keep"]),
                         ("A03", self.leaf["tax1_id"], 2, "COLD"))  # Tax / Inventory / Keep unchanged
        cleared = svc.update_item(self.db, "category3", self.c3["id"], {**self.leaf, "warehouse_location": "  "}, 1)
        self.assertEqual((cleared["sales_location"], cleared["warehouse_location"]), ("A03", None))
        self.assertIn("sales_location", self.errors(lambda: svc.update_item(
            self.db, "category3", self.c3["id"], {**self.leaf, "sales_location": "X" * 51}, 1)))
        self.assertIn("not an active Sales Floor Location", self.errors(lambda: svc.update_item(
            self.db, "category3", self.c3["id"], {**self.leaf, "sales_location": "x" * 50}, 1))["sales_location"])
        self.assertIn("not an active Warehouse Location", self.errors(lambda: svc.update_item(
            self.db, "category3", self.c3["id"], {**self.leaf, "warehouse_location": "A03"}, 1))["warehouse_location"])
        self.assertEqual(svc.update_item(self.db, "category3", self.c3["id"], {**self.leaf, "sales_location": "x" * 30}, 1)["sales_location"], "X" * 30)
        option = next(c for c in svc.lookup_options(self.db)["category3"] if c["id"] == self.c3["id"])
        self.assertEqual(option["sales_location"], "X" * 30)  # the MD form reads it for the inherited hint
        headers, data = svc.export_table(self.db, "category3")
        row = dict(zip(headers, data[0]))
        self.assertEqual((row["Sales Floor Location"], row["Warehouse Location"]), ("X" * 30, ""))

    def test_md_keeps_a_saved_code_that_no_store_has_and_inactive_codes_cannot_be_new(self):
        from backend.services import master_group as svc
        item = self.md(sales_location="A03")
        self.db.get(svc.MdMaster, item["id"]).c_warehouse_location = "LEGACY-9"  # earlier free text
        self.db.commit()
        kept = svc.update_item(self.db, "md", item["id"], {**self.md_body, "second_name": "Alt", "sales_location": "A03",
                                                           "warehouse_location": "LEGACY-9"}, 1)
        self.assertEqual((kept["second_name"], kept["warehouse_location"]), ("Alt", "LEGACY-9"))  # never blocks the save
        self.db.query(StoreLocation).filter(StoreLocation.c_location_code == "B05").update({"b_active": False})
        self.db.commit()
        self.assertIn("sales_location", self.errors(lambda: svc.update_item(
            self.db, "md", item["id"], {**self.md_body, "sales_location": "B05"}, 1)))  # inactive everywhere
        self.assertEqual(self.db.get(svc.MdMaster, item["id"]).c_sales_location, "A03")

    def test_md_inherits_overrides_and_reset_returns_to_category_default(self):
        from backend.services import master_group as svc
        item = self.md()
        self.assertEqual((item["sales_location"], item["effective_sales_location"], item["sales_location_source"]), (None, "A03", "category"))
        self.assertEqual((item["effective_warehouse_location"], item["warehouse_location_source"]), ("W01-R02-L03", "category"))
        # Override one area only (an active Location Code, upper-cased); the other still inherits.
        item = svc.update_item(self.db, "md", item["id"], {**self.md_body, "sales_location": "a03-b02-s04"}, 1)
        self.assertEqual((item["sales_location"], item["effective_sales_location"], item["sales_location_source"]), ("A03-B02-S04", "A03-B02-S04", "item"))
        self.assertEqual(item["warehouse_location_source"], "category")
        # Saving other fields without the keys keeps the Override; Reset (blank) = NULL = inherit again.
        item = svc.update_item(self.db, "md", item["id"], {**self.md_body, "second_name": "Alt"}, 1)
        self.assertEqual(item["sales_location"], "A03-B02-S04")
        item = svc.update_item(self.db, "md", item["id"], {**self.md_body, "sales_location": ""}, 1)
        self.assertEqual((item["sales_location"], item["effective_sales_location"], item["sales_location_source"]), (None, "A03", "category"))
        self.assertIsNone(self.db.get(svc.MdMaster, item["id"]).c_sales_location)
        # Category III default change reaches inheriting MDs; without any value the area is not set.
        svc.update_item(self.db, "category3", self.c3["id"], {**self.leaf, "sales_location": "b05", "warehouse_location": ""}, 1)
        item = svc.serialize(self.db, "md", self.db.get(svc.MdMaster, item["id"]))
        self.assertEqual((item["effective_sales_location"], item["effective_warehouse_location"], item["warehouse_location_source"]), ("B05", None, "none"))
        headers, data = svc.export_table(self.db, "md")
        row = dict(zip(headers, data[0]))
        self.assertEqual((row["Sales Floor Location"], row["Warehouse Location"]), ("B05 (Category III)", ""))
        # Barcode / Default Detail sync / Color untouched; deprecated Store tables never written.
        default = next(d for d in svc.list_md_details(self.db, item["id"]) if d["is_default"])
        self.assertEqual((default["unit"], default["price"]), ("EA", "1.00"))
        self.assertEqual((self.db.query(StoreMdLocation).count(), self.db.query(StoreCategory3Location).count()), (0, 0))

    def test_recommended_location_is_not_stock_and_not_a_store_location(self):
        from backend.services import master_group as svc
        before = self.db.query(StoreLocation).count()
        self.md(sales_location="A03")
        # Recommended text never creates Store locations or links.
        self.assertEqual((self.db.query(StoreLocation).count(), self.db.query(StoreMdLocation).count()), (before, 0))
        self.assertEqual(svc.list_items(self.db, "location", store_id=1)["total"], 0)


class ShelfMasterPortalApiTests(unittest.TestCase):
    """Platform / Merchant / Store scope and the Use Location Management option, checked on the server."""

    def setUp(self):
        from backend.models_admin.merchant import Merchant
        from backend.models_admin.store import Store
        from backend.routers import master_group, platform_store
        from tools import test_registration_email
        test_registration_email.RegistrationEmailTests.setUp(self)
        self.app.include_router(master_group.router)
        self.app.dependency_overrides[master_group.get_db] = self.app.dependency_overrides[platform_store.get_db]
        for owner in ("MERCHANT", "STORE"):
            test_registration_email.RegistrationEmailTests.activate(self, owner)
        with self.factory() as db:
            db.add(Merchant(i_merchant_id=11, c_merchant_code="MER26EFGH", c_merchant_name="Other Merchant", c_status="active"))
            db.add(Store(i_store_id=11, i_merchant_id=10, c_store_code="STR26SIBL", c_store_name="Sibling", c_status="active"))
            db.add(Store(i_store_id=12, i_merchant_id=11, c_store_code="STR26OTHR", c_store_name="Foreign", c_status="active"))
            db.add(Store(i_store_id=13, i_merchant_id=11, c_store_code="STR26GONE", c_store_name="Closed", c_status="inactive"))
            db.commit()
            # STEP 3-3C: these scope rules apply to a Merchant contracted for Use Location Management (Business
            # Option, ADDON); without it the feature is refused (tools.test_feature_enforcement).
            from backend.services.merchant_features import save_business_options
            save_business_options(db, 10, {"location_management": True}, 1)
            db.commit()

    def login(self, scope):
        codes = {"MERCHANT": "MER26ABCD", "STORE": "STR26ABCD"}
        response = self.client.post("/auth/login", data={
            "store_code": codes[scope], "email": "primary@example.test", "password": "Awsum@5858",
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)

    def set_option(self, on):
        from backend.models_admin.store import Store
        with self.factory() as db:
            db.get(Store, 10).b_opt_use_shelf_location = on
            db.commit()

    def test_platform_any_store_with_store_selector(self):
        api = "/platform/api/master-group/location"
        self.assertEqual(self.client.get(api).status_code, 400)  # a Store must be selected
        self.assertEqual(self.client.get(api + "?store_id=999").status_code, 404)
        made = self.client.post(api + "?store_id=12", json={"code": "f-1", "name": "Front"})
        self.assertEqual(made.status_code, 201, made.text)
        self.assertEqual(made.json()["item"]["store_id"], 12)
        self.assertEqual(self.client.post(api + "?store_id=12", json={"code": "F-1", "name": "Dup"}).status_code, 422)
        self.assertEqual(self.client.post(api + "?store_id=13", json={"code": "OLD", "name": "Old"}).status_code, 201)
        options = self.client.get("/platform/api/master-group/options").json()
        stores = {s["id"]: s for s in options["stores"]}
        # Active Stores + the inactive Store that already has Shelf data; the option flag is reported per Store.
        self.assertIn(13, stores)
        self.assertFalse(stores[13]["active"])
        self.assertEqual((stores[12]["merchant_name"], stores[12]["location_management"], options["store_fixed"]), ("Other Merchant", False, None))
        self.assertEqual(self.client.get("/platform/master-group/location-master").status_code, 200)
        # The earlier Shelf Location URL redirects to Location Master (query kept).
        moved = self.client.get("/platform/master-group/shelf-location?store_id=12", follow_redirects=False)
        self.assertEqual((moved.status_code, moved.headers["location"]), (307, "/platform/master-group/location-master?store_id=12"))
        report = self.client.get(api + "/export?format=csv&store_id=12")
        self.assertEqual(report.status_code, 200)
        self.assertIn("F-1", report.text)

    def test_two_column_list_filters_one_area_per_request(self):
        """Location Master list: optional location_area (Sales Floor / Warehouse), code ascending, own paging per
        Area, same Search / Status for both; still one Store's data only."""
        api = "/platform/api/master-group/location"
        for store_id, code, area in ((12, "W01-B02-C01", "WAREHOUSE"), (12, "A03-B01-C01", "SALES_FLOOR"),
                                     (12, "A01-B01-C01", "SALES_FLOOR"), (11, "A02-B01-C01", "SALES_FLOOR")):
            made = self.client.post(f"{api}?store_id={store_id}", json={"code": code, "name": code, "location_area": area})
            self.assertEqual(made.status_code, 201, made.text)
        listed = lambda **q: self.client.get(api, params={"store_id": 12, "sort": "code", **q}).json()
        sales, warehouse = listed(location_area="SALES_FLOOR"), listed(location_area="warehouse")
        self.assertEqual(([i["code"] for i in sales["items"]], sales["total"]), (["A01-B01-C01", "A03-B01-C01"], 2))
        self.assertEqual([i["code"] for i in warehouse["items"]], ["W01-B02-C01"])  # case-insensitive Area
        self.assertEqual(listed()["total"], 3)  # without the filter: both Areas (Export / earlier clients)
        self.assertEqual([i["code"] for i in listed(location_area="SALES_FLOOR", q="A03")["items"]], ["A03-B01-C01"])
        paged = listed(location_area="SALES_FLOOR", size=1, page=2)
        self.assertEqual((paged["page"], paged["pages"], [i["code"] for i in paged["items"]]), (2, 2, ["A03-B01-C01"]))
        self.assertNotIn("A02-B01-C01", [i["code"] for i in listed(location_area="SALES_FLOOR")["items"]])  # Store 11's
        bad = self.client.get(api, params={"store_id": 12, "location_area": "ROOF"})
        self.assertEqual((bad.status_code, bad.json()["errors"]), (422, {"location_area": "Select Sales Floor or Warehouse."}))
        self.assertEqual(self.client.get("/platform/api/master-group/tax", params={"location_area": "WAREHOUSE"}).status_code, 422)

    def test_range_preview_and_create_through_the_store_scope(self):
        api = "/platform/api/master-group/location/range"
        body = {"location_area": "SALES_FLOOR", "zone": "S01", "bay_from": 1, "bay_to": 2, "shelf_from": 1, "shelf_to": 2}
        self.assertEqual(self.client.post(api + "/preview", json=body).status_code, 400)  # a Store must be selected
        self.assertEqual(self.client.post("/platform/api/master-group/location?store_id=12",
                                          json={"code": "S01-B02-C02", "location_area": "SALES_FLOOR"}).status_code, 201)
        preview = self.client.post(api + "/preview?store_id=12", json=body)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual({k: preview.json()[k] for k in ("total", "duplicates", "new")}, {"total": 4, "duplicates": 1, "new": 3})
        made = self.client.post(api + "?store_id=12", json=body)
        self.assertEqual((made.status_code, made.json()["created"], made.json()["skipped"]), (201, 3, ["S01-B02-C02"]))
        self.assertEqual(self.client.post(api + "?store_id=12", json=body).json()["created"], 0)  # all exist now
        bad = self.client.post(api + "?store_id=12", json={**body, "bay_from": 3})
        self.assertEqual((bad.status_code, list(bad.json()["errors"])), (422, ["bay_to"]))
        listed = self.client.get("/platform/api/master-group/location", params={"store_id": 12, "sort": "code", "size": 50}).json()
        self.assertEqual([i["code"] for i in listed["items"]], ["S01-B01-C01", "S01-B01-C02", "S01-B02-C01", "S01-B02-C02"])
        self.assertEqual(self.client.post(api + "?store_id=11", json=body).json()["created"], 4)  # Store isolation

    def test_initialize_on_first_visit_through_the_store_scope(self):
        api = "/platform/api/master-group/location/initialize"
        self.assertEqual(self.client.post(api).status_code, 400)            # a Store must be selected
        first = self.client.post(api + "?store_id=12")
        self.assertEqual((first.status_code, first.json()), (200, {"initialized": True, "created": 20, "reason": "created"}))
        self.assertEqual(self.client.post(api + "?store_id=12").json()["created"], 0)
        listed = self.client.get("/platform/api/master-group/location",
                                 params={"store_id": 12, "location_area": "WAREHOUSE", "sort": "code", "size": 50}).json()
        self.assertEqual([i["code"] for i in listed["items"]], [f"W01-R{n:02d}-C01" for n in range(1, 11)])
        self.assertEqual(self.client.get("/platform/api/master-group/location", params={"store_id": 11}).json()["total"], 0)
        self.login("MERCHANT")
        self.assertEqual(self.client.post("/merchant/api/master-group/location/initialize?store_id=13").status_code, 403)

    def test_md_detail_link_api_is_removed(self):
        self.assertEqual(self.client.get("/platform/api/master-group/md/1/locations?store_id=12").status_code, 404)
        self.assertIn(self.client.put("/platform/api/master-group/md/1/details/1/locations?store_id=12",
                                      json={"areas": {}}).status_code, (404, 405))

    def add_locations(self, rows):
        """(store, code, area, active, name) rows straight into tb_store_location."""
        from backend.models_admin.store_location import StoreLocation
        with self.factory() as db:
            db.add_all([StoreLocation(i_store_id=s, c_location_code=c, c_location_name=n or c, c_location_area=a, b_active=on)
                        for s, c, a, on, n in rows])
            db.commit()

    LOOKUP_ROWS = (
        (10, "S01-B01-C01", "SALES_FLOOR", True, None), (10, "S01-B02-C01", "SALES_FLOOR", False, None),
        (10, "W01-R01-C01", "WAREHOUSE", True, None),
        (11, "S01-B01-C01", "SALES_FLOOR", False, None), (11, "S01-B03-C01", "SALES_FLOOR", True, "Front aisle"),
        (12, "S01-B09-C01", "SALES_FLOOR", True, None),
    )

    def codes(self, prefix, **params):
        res = self.client.get(f"/{prefix}/api/master-group/location/codes", params={"location_area": "SALES_FLOOR", **params})
        return res

    def test_location_code_lookup_all_stores_counts_area_search_paging(self):
        self.add_locations(self.LOOKUP_ROWS)
        data = self.codes("platform").json()  # Platform, All Stores, Active (default)
        self.assertEqual([(i["code"], i["stores"], i["active_stores"]) for i in data["items"]],
                         [("S01-B01-C01", 2, 1), ("S01-B03-C01", 1, 1), ("S01-B09-C01", 1, 1)])  # each code once
        from backend.models_admin.store import Store
        with self.factory() as db:
            every_store = db.query(Store).count()
        self.assertEqual((data["total"], data["store_total"], data["location_area"]), (3, every_store, "SALES_FLOOR"))
        everything = self.codes("platform", active="").json()["items"]
        self.assertEqual([(i["code"], i["active"]) for i in everything if not i["active"]], [("S01-B02-C01", False)])
        self.assertEqual([i["code"] for i in self.codes("platform", active="inactive").json()["items"]], ["S01-B02-C01"])
        self.assertEqual([i["code"] for i in self.codes("platform", location_area="warehouse").json()["items"]], ["W01-R01-C01"])
        for term in ("b03", "FRONT"):  # Code or Name, any case
            self.assertEqual([i["code"] for i in self.codes("platform", q=term).json()["items"]], ["S01-B03-C01"])
        paged = self.codes("platform", size=1, page=2).json()
        self.assertEqual((paged["page"], paged["pages"], [i["code"] for i in paged["items"]]), (2, 3, ["S01-B03-C01"]))
        self.assertEqual(self.codes("platform", size=1, page=99).json()["page"], 3)  # clamped to the last page
        one = self.codes("platform", store_id=11).json()  # Store filter: that Store only
        self.assertEqual([(i["code"], i["stores"]) for i in one["items"]], [("S01-B03-C01", 1)])
        self.assertEqual(one["store_total"], 1)
        self.assertEqual(self.codes("platform", location_area="ROOF").status_code, 422)
        self.assertEqual(self.codes("platform", active="maybe").status_code, 422)
        self.assertEqual(self.codes("platform", store_id=999).status_code, 404)

    def test_location_code_lookup_merchant_and_store_scope(self):
        self.add_locations(self.LOOKUP_ROWS)
        self.login("MERCHANT")  # Merchant 10: Stores 10 and 11 only
        data = self.codes("merchant").json()
        self.assertEqual([(i["code"], i["stores"], i["active_stores"]) for i in data["items"]],
                         [("S01-B01-C01", 2, 1), ("S01-B03-C01", 1, 1)])
        self.assertEqual(data["store_total"], 2)
        self.assertEqual(self.codes("merchant", store_id=12).status_code, 403)  # another Merchant's Store
        self.login("STORE")  # Store 10: its own Store, only with Use Location Management ON
        self.assertEqual(self.codes("store").status_code, 403)
        self.set_option(True)
        self.assertEqual([i["code"] for i in self.codes("store").json()["items"]], ["S01-B01-C01"])
        self.assertEqual(self.codes("store", store_id=11).status_code, 403)

    def test_category3_save_accepts_only_active_codes_of_its_scope_and_keeps_saved_values(self):
        from backend.models_admin.master_group import Category3
        from backend.services import master_group as svc
        self.add_locations(self.LOOKUP_ROWS)
        with self.factory() as db:
            c1 = svc.create_item(db, "category1", {"name": "Mine"}, 1, merchant_id=10)
            c2 = svc.create_item(db, "category2", {"name": "Mine2", "category1_id": c1["id"]}, 1, merchant_id=10)
            c3 = svc.create_item(db, "category3", {"name": "Mine3", "category1_id": c1["id"], "category2_id": c2["id"]}, 1, merchant_id=10)
            row = db.get(Category3, c3["id"])
            row.c_sales_location = "OLD-CODE"  # saved earlier as free text; no Store has it
            db.commit()
        body = {"name": "Mine3", "category1_id": c1["id"], "category2_id": c2["id"]}
        current = lambda: (lambda r: (r.c_category_name, r.c_sales_location, r.c_warehouse_location))(self.factory().get(Category3, c3["id"]))
        self.login("MERCHANT")
        api = f"/merchant/api/master-group/category3/{c3['id']}"
        # The saved value is kept and never blocks other fields.
        self.assertEqual(self.client.put(api, json={**body, "name": "Renamed", "sales_location": "OLD-CODE"}).status_code, 200)
        self.assertEqual(current(), ("Renamed", "OLD-CODE", None))
        body["name"] = "Renamed"
        # A new value must be an active location of the field's Area in this Merchant's Stores.
        for code, key in (("S01-B09-C01", "sales_location"),      # another Merchant's Store
                          ("S01-B02-C01", "sales_location"),      # inactive everywhere
                          ("W01-R01-C01", "sales_location"),      # the other Area
                          ("S01-B03-C01", "warehouse_location"),  # the other Area
                          ("NOPE", "sales_location")):
            with self.subTest(code=code, key=key):
                bad = self.client.put(api, json={**body, key: code})
                self.assertEqual((bad.status_code, list(bad.json()["errors"])), (422, [key]))
        self.assertEqual(current(), ("Renamed", "OLD-CODE", None))
        ok = self.client.put(api, json={**body, "sales_location": "s01-b01-c01", "warehouse_location": "W01-R01-C01"})
        self.assertEqual(ok.status_code, 200, ok.text)  # active in Store 10 (inactive in Store 11 does not matter)
        self.assertEqual(current(), ("Renamed", "S01-B01-C01", "W01-R01-C01"))
        self.assertEqual(self.client.put(api, json={**body, "sales_location": ""}).status_code, 200)  # Clear = Not set
        self.assertEqual(current(), ("Renamed", None, "W01-R01-C01"))

    def test_store_with_location_management_off_never_changes_common_locations(self):
        from backend.models_admin.master_group import Category3
        from backend.services import master_group as svc
        self.add_locations([(10, "A03", "SALES_FLOOR", True, None), (10, "B02", "SALES_FLOOR", True, None),
                            (10, "W01", "WAREHOUSE", True, None)])
        with self.factory() as db:
            c1 = svc.create_item(db, "category1", {"name": "Mine"}, 1, merchant_id=10)
            c2 = svc.create_item(db, "category2", {"name": "Mine2", "category1_id": c1["id"]}, 1, merchant_id=10)
            c3 = svc.create_item(db, "category3", {"name": "Mine3", "category1_id": c1["id"], "category2_id": c2["id"],
                                                   "sales_location": "A03", "warehouse_location": "W01"}, 1, merchant_id=10)
        body = {"name": "Mine3", "category1_id": c1["id"], "category2_id": c2["id"]}
        current = lambda: (lambda r: (r.c_sales_location, r.c_warehouse_location))(self.factory().get(Category3, c3["id"]))
        # Store login, option OFF: the values cannot be changed or cleared (the keys are dropped on the server).
        self.login("STORE")
        res = self.client.put(f"/store/api/master-group/category3/{c3['id']}", json={**body, "sales_location": "", "warehouse_location": "X9"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(current(), ("A03", "W01"))
        # Option ON: the Store's normal Category III permission applies.
        self.set_option(True)
        self.client.put(f"/store/api/master-group/category3/{c3['id']}", json={**body, "sales_location": "b02"})
        self.assertEqual(current(), ("B02", "W01"))
        # Merchant: always (its own Category III).
        self.login("MERCHANT")
        self.client.put(f"/merchant/api/master-group/category3/{c3['id']}", json={**body, "warehouse_location": ""})
        self.assertEqual(current(), ("B02", None))

    def test_category3_default_location_api_is_removed(self):
        for scope in ("platform",):
            self.assertEqual(self.client.get(f"/{scope}/api/master-group/category3/1/locations?store_id=12").status_code, 404)
            self.assertIn(self.client.put(f"/{scope}/api/master-group/category3/1/locations?store_id=12",
                                          json={"sales_location_id": None}).status_code, (404, 405))

    def test_merchant_manages_only_its_stores(self):
        self.login("MERCHANT")
        api = "/merchant/api/master-group/location"
        self.assertEqual(self.client.post(api + "?store_id=11", json={"code": "S-1", "name": "Sibling shelf"}).status_code, 201)
        for foreign in (12, 13):
            self.assertEqual(self.client.get(f"{api}?store_id={foreign}").status_code, 403)
            self.assertEqual(self.client.post(f"{api}/range?store_id={foreign}", json={"location_area": "WAREHOUSE"}).status_code, 403)
            self.assertEqual(self.client.post(f"{api}?store_id={foreign}", json={"code": "X", "name": "X"}).status_code, 403)
        ids = [s["id"] for s in self.client.get("/merchant/api/master-group/options").json()["stores"]]
        self.assertEqual(sorted(ids), [10, 11])
        page = self.client.get("/merchant/master-group/location-master")
        self.assertEqual(page.status_code, 200)
        self.assertIn('href="/merchant/master-group/location-master"', page.text)  # Merchant: always available
        self.assertIn('id="mgStore"', page.text)

    def test_store_login_own_store_only_and_option_off_blocks_but_keeps_data(self):
        from backend.models_admin.store_location import StoreLocation
        self.login("STORE")
        api = "/store/api/master-group/location"
        # OFF (default): menu hidden, page and API blocked.
        self.assertEqual(self.client.get(api).status_code, 403)
        self.assertEqual(self.client.get("/store/master-group/location-master").status_code, 403)
        self.assertEqual(self.client.post("/store/api/master-group/location/initialize").status_code, 403)
        self.assertNotIn("location-master", self.client.get("/store/master-group/tax").text)
        self.set_option(True)
        self.assertIn('href="/store/master-group/location-master"', self.client.get("/store/master-group/tax").text)
        made = self.client.post(api, json={"code": "B-1", "name": "Backroom", "location_area": "WAREHOUSE", "location_type": "ROOM"})
        self.assertEqual(made.status_code, 201, made.text)
        self.assertEqual(made.json()["item"]["store_id"], 10)  # always its own Store
        self.assertEqual(self.client.get(api + "?store_id=11").status_code, 403)  # never another Store
        options = self.client.get("/store/api/master-group/options").json()
        self.assertEqual((options["store_fixed"], [s["id"] for s in options["stores"]]), (10, [10]))
        # OFF again: blocked, saved data kept.
        self.set_option(False)
        self.assertEqual(self.client.get(api).status_code, 403)
        with self.factory() as db:
            self.assertEqual([r.c_location_code for r in db.query(StoreLocation).filter(StoreLocation.i_store_id == 10)], ["B-1"])


if __name__ == "__main__":
    unittest.main()
