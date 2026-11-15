"""SO-1b: Store Override common service (services.store_md_settings).

Isolated SQLite (foreign keys enforced), the SO-1a fixture: Merchant 10 owns Stores 100 / 101 and MD 1 (Details 11
price 5.00 Default, 12 price 9.00); Merchant 20 owns Store 200 and MD 2 (Detail 21). No real database is touched.
"""
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from backend.database.pg_platform import PlatformBase
from backend.models_admin.master_group import MdDetail, MdMaster
from backend.models_admin.store_md import StoreMd, StoreMdPrice
from backend.services import store_md_settings as so
from backend.services.master_group import Conflict, FieldErrors, NotFound
from tools.test_store_md_schema import seed, sqlite_engine

M10_STORES = (100, 101)


class StoreMdSettingsTests(unittest.TestCase):
    def setUp(self):
        self.engine = sqlite_engine()
        self.addCleanup(self.engine.dispose)
        PlatformBase.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.addCleanup(self.db.close)
        seed(self.db)
        md = self.db.get(MdMaster, 1)
        md.dt_updated, md.i_updated_by = datetime(2026, 1, 2, 3, 4, 5), 99  # the common MD's last change
        self.db.commit()

    def grid(self, stores=M10_STORES, **kw):
        return so.get_store_settings(self.db, 1, merchant_id=10, store_ids=stores, **kw)

    def save(self, prices=(), visibility=(), user=7, stores=M10_STORES, **kw):
        return so.save_store_settings(self.db, 1, {"prices": list(prices), "visibility": list(visibility)}, user,
                                      merchant_id=10, store_ids=stores, **kw)

    @staticmethod
    def cell(grid, store, detail):
        return next(s for s in grid["stores"] if s["id"] == store)["prices"][str(detail)]

    def price(self, store, detail, price, base="0"):
        return {"store_id": store, "detail_id": detail, "price": price, "base_version": base}

    # ---- read / inherit ----
    def test_grid_inherits_merchant_prices_and_visibility_by_default(self):
        grid = self.grid()
        self.assertEqual([(d["id"], d["merchant_price"], d["is_default"]) for d in grid["details"]],
                         [(11, "5.00", True), (12, "9.00", False)])
        self.assertEqual([s["id"] for s in grid["stores"]], [100, 101])
        self.assertEqual(self.cell(grid, 100, 11), {"price": "5.00", "source": "merchant", "override": None, "version": "0"})
        self.assertTrue(all(s["pos_visible"] and s["pos_version"] == "0" for s in grid["stores"]))
        self.assertEqual(so.resolve_store_prices(self.db, 100, [11, 12]),
                         {11: {"price": Decimal("5.00"), "source": "merchant"}, 12: {"price": Decimal("9.00"), "source": "merchant"}})

    def test_override_equal_or_zero_is_kept_and_audited_on_the_override_row_only(self):
        grid = self.save([self.price(100, 11, "5.00"), self.price(101, 11, "0"), self.price(100, 12, "12.5")])
        self.assertEqual(self.cell(grid, 100, 11)["source"], "store")  # equal to the Merchant price: still an override
        self.assertEqual(self.cell(grid, 101, 11)["price"], "0.00")  # $0.00 is valid
        self.assertEqual(self.cell(grid, 101, 12)["source"], "merchant")
        self.assertEqual(so.resolve_store_prices(self.db, 101, [11, 12]),
                         {11: {"price": Decimal("0.00"), "source": "store"}, 12: {"price": Decimal("9.00"), "source": "merchant"}})
        row = self.db.query(StoreMdPrice).filter_by(i_store_id=100, i_md_detail_id=12).one()
        self.assertEqual((row.n_price, row.i_row_version, row.i_created_by, row.i_updated_by, row.i_owner_key),
                         (Decimal("12.50"), 1, 7, 7, 10))
        md, detail = self.db.get(MdMaster, 1), self.db.get(MdDetail, 12)
        self.assertEqual((md.dt_updated, md.i_updated_by, detail.n_price), (datetime(2026, 1, 2, 3, 4, 5), 99, Decimal("9.00")))
        # A later edit by another user bumps the version and the updater; Created By stays.
        version = self.cell(grid, 100, 12)["version"]
        grid = self.save([self.price(100, 12, "13", version)], user=8)
        self.db.refresh(row)
        self.assertEqual((row.n_price, row.i_row_version, row.i_created_by, row.i_updated_by), (Decimal("13.00"), 2, 7, 8))
        self.assertEqual(self.cell(grid, 100, 12)["version"], f"{row.i_store_md_price_id}.2")

    def test_revert_deletes_only_that_override(self):
        grid = self.save([self.price(100, 11, "4"), self.price(101, 11, "3")])
        grid = self.save([self.price(100, 11, None, self.cell(grid, 100, 11)["version"]),
                          self.price(100, 12, None)])  # revert of an inherited cell: nothing to delete
        self.assertEqual(self.cell(grid, 100, 11), {"price": "5.00", "source": "merchant", "override": None, "version": "0"})
        self.assertEqual(self.cell(grid, 101, 11)["price"], "3.00")  # Store B keeps its own override
        self.assertEqual(self.db.query(StoreMdPrice).count(), 1)

    def test_merchant_price_change_flows_to_inheriting_stores_only(self):
        self.save([self.price(100, 11, "4")])
        self.db.get(MdDetail, 11).n_price = Decimal("6.00")  # the Merchant changes its price (MD / Detail save)
        self.db.commit()
        grid = self.grid()
        self.assertEqual((self.cell(grid, 100, 11)["price"], self.cell(grid, 101, 11)["price"]), ("4.00", "6.00"))

    # ---- POS visibility ----
    def test_pos_hidden_creates_a_row_and_visible_removes_it(self):
        grid = self.save(visibility=[{"store_id": 100, "pos_visible": False, "base_version": "0"}])
        store = next(s for s in grid["stores"] if s["id"] == 100)
        self.assertFalse(store["pos_visible"])
        self.assertEqual(so.resolve_pos_visible(self.db, 100, [1]), {1: False})
        self.assertEqual(so.resolve_pos_visible(self.db, 101, [1]), {1: True})  # Store B unaffected
        row = self.db.query(StoreMd).one()
        self.assertEqual((row.i_store_id, row.b_pos_visible, row.i_created_by), (100, False, 7))
        self.save(visibility=[{"store_id": 100, "pos_visible": True, "base_version": store["pos_version"]}])
        self.assertEqual(self.db.query(StoreMd).count(), 0)
        self.db.get(MdMaster, 1).b_active = False  # an inactive common MD is never shown, whatever the Store says
        self.db.commit()
        self.assertEqual(so.resolve_pos_visible(self.db, 101, [1]), {1: False})

    # ---- scope / ownership ----
    def test_merchant_and_store_scope_are_enforced(self):
        with self.assertRaises(NotFound):  # another Merchant's MD
            so.get_store_settings(self.db, 1, merchant_id=20, store_ids=[200])
        with self.assertRaises(NotFound):  # Platform scope: no Stores
            so.get_store_settings(self.db, 1, merchant_id=None, store_ids=[100])
        self.assertEqual([s["id"] for s in self.grid(stores=[100, 200])["stores"]], [100])  # foreign Store dropped
        self.assertEqual([s["id"] for s in self.grid(stores=[101])["stores"]], [101])  # a Store login: its own only
        for label, prices, stores in (("another Merchant's Store", [self.price(200, 11, "1")], [100, 200]),
                                      ("a Store outside the login scope", [self.price(101, 11, "1")], [100]),
                                      ("a Detail of another MD", [self.price(100, 21, "1")], M10_STORES)):
            with self.subTest(label), self.assertRaises(NotFound):
                self.save(prices, stores=stores)
            self.db.rollback()
        self.assertEqual(self.db.query(StoreMdPrice).count(), 0)

    def test_read_only_store_and_invalid_input_save_nothing(self):
        with self.assertRaises(so.StoreReadOnly):  # one transaction: the valid cell is not saved either
            self.save([self.price(100, 11, "1"), self.price(101, 11, "2")], read_only_store_ids=[101])
        self.db.rollback()
        cases = {
            "negative": [self.price(100, 11, "-1")],
            "three decimals": [self.price(100, 11, "1.005")],
            "too large": [self.price(100, 11, "99999999999")],
            "not a number": [self.price(100, 11, "abc")],
            "same cell twice": [self.price(100, 11, "1"), self.price(100, 11, "2")],
            "no Store": [{"detail_id": 11, "price": "1"}],
        }
        for label, prices in cases.items():
            with self.subTest(label), self.assertRaises(FieldErrors):
                self.save(prices)
        with self.assertRaises(FieldErrors):
            self.save(visibility=[{"store_id": 100, "pos_visible": "no"}])
        self.assertEqual((self.db.query(StoreMdPrice).count(), self.db.query(StoreMd).count()), (0, 0))

    # ---- concurrency ----
    def test_stale_version_conflicts_and_saves_nothing(self):
        first = self.grid()
        self.save([self.price(100, 11, "4")], user=8)  # another user saved this cell first
        with self.assertRaises(so.VersionConflict) as ctx:
            self.save([self.price(100, 12, "7"), self.price(100, 11, "3", self.cell(first, 100, 11)["version"])])
        self.assertEqual([(c["store_id"], c["detail_id"], c["price"], c["source"]) for c in ctx.exception.conflicts],
                         [(100, 11, "4.00", "store")])
        self.db.rollback()
        grid = self.grid()
        self.assertEqual((self.cell(grid, 100, 11)["price"], self.cell(grid, 100, 12)["source"]), ("4.00", "merchant"))

    def test_delete_and_recreate_never_matches_an_old_token(self):
        grid = self.save([self.price(100, 11, "4")])
        old = self.cell(grid, 100, 11)["version"]
        grid = self.save([self.price(100, 11, None, old)], user=8)  # reverted ...
        self.save([self.price(100, 11, "4", self.cell(grid, 100, 11)["version"])], user=8)  # ... and created again
        new = self.cell(self.grid(), 100, 11)["version"]
        self.assertNotEqual(old, new)
        self.assertTrue(old.endswith(".1") and new.endswith(".1"))  # same row version, different row
        with self.assertRaises(so.VersionConflict):
            self.save([self.price(100, 11, "9", old)])
        self.db.rollback()
        self.assertEqual(self.cell(self.grid(), 100, 11)["price"], "4.00")

    def test_concurrent_first_creation_conflicts(self):
        # Both users read the cell without a row ("0"); the second save must not overwrite the first.
        self.save([self.price(100, 11, "4")], user=8)
        with self.assertRaises(so.VersionConflict):
            self.save([self.price(100, 11, "3", "0")])
        self.db.rollback()
        with self.assertRaises(so.VersionConflict):  # same for POS visibility
            self.save(visibility=[{"store_id": 101, "pos_visible": False, "base_version": "0"}], user=8)
            self.save(visibility=[{"store_id": 101, "pos_visible": False, "base_version": "0"}])
        self.db.rollback()

    def test_unique_violation_of_a_concurrent_insert_is_a_conflict(self):
        # Two transactions both read the cell without a row: the other one commits its INSERT after this save read
        # the rows, so this INSERT hits the unique key at commit (as on PostgreSQL, SO-1a rehearsal). It must become
        # a 409 with nothing of this request saved.
        import tempfile
        from pathlib import Path
        from sqlalchemy import create_engine, event, text

        folder = tempfile.TemporaryDirectory(prefix="awsum-store-md-")
        self.addCleanup(folder.cleanup)
        url = f"sqlite:///{Path(folder.name) / 'so.db'}"
        engines = [create_engine(url), create_engine(url)]
        for engine in engines:
            event.listen(engine, "connect", lambda conn, _r: conn.execute("PRAGMA foreign_keys=ON"))
            self.addCleanup(engine.dispose)
        PlatformBase.metadata.create_all(engines[0])
        with sessionmaker(bind=engines[0])() as db:
            seed(db)
        mine, other = sessionmaker(bind=engines[0])(), engines[1]
        self.addCleanup(mine.close)
        real_stamp = so._stamp

        def concurrent_insert(row, user_id, *, created):
            if created and isinstance(row, StoreMdPrice) and row.i_md_detail_id == 11:
                with other.begin() as conn:
                    conn.execute(text("INSERT INTO tb_store_md_price (i_store_id, i_md_id, i_md_detail_id, i_owner_key, "
                                      "n_price, i_row_version) VALUES (100, 1, 11, 10, 4, 1)"))
            real_stamp(row, user_id, created=created)
        with patch.object(so, "_stamp", concurrent_insert), self.assertRaises(so.VersionConflict) as ctx:
            so.save_store_settings(mine, 1, {"prices": [self.price(100, 11, "3"), self.price(100, 12, "7")]}, 7,
                                   merchant_id=10, store_ids=M10_STORES)
        self.assertEqual([(c["detail_id"], c["price"], c["source"]) for c in ctx.exception.conflicts],
                         [(11, "4.00", "store")])
        mine.rollback()
        with other.connect() as conn:  # only the other transaction's row; Detail 12 was not saved
            self.assertEqual(conn.execute(text("SELECT i_md_detail_id, n_price FROM tb_store_md_price")).all(), [(11, 4)])

    # ---- Store Merchant change ----
    def test_store_with_overrides_cannot_change_merchant(self):
        so.ensure_store_merchant_change_allowed(self.db, 100)  # no rows yet: allowed
        self.save(visibility=[{"store_id": 100, "pos_visible": False, "base_version": "0"}])
        self.assertEqual(so.store_override_count(self.db, 100), 1)
        with self.assertRaises(Conflict) as ctx:
            so.ensure_store_merchant_change_allowed(self.db, 100)
        self.assertEqual(str(ctx.exception), so.STORE_MERCHANT_LOCKED)
        so.ensure_store_merchant_change_allowed(self.db, 101)


if __name__ == "__main__":
    unittest.main()
