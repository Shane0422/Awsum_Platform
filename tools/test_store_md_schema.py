"""SO-1a: Store Override tables (tb_store_md_price, tb_store_md) - constraints and the additive migration.

Isolated SQLite databases with foreign keys enforced (PRAGMA foreign_keys=ON). Merchant 10 owns Stores 100 / 101 and
MD 1 (Details 11, 12); Merchant 20 owns Store 200 and MD 2 (Detail 21). No real database is touched; the PostgreSQL
rehearsal runs on a temporary copy (see the SO-1a report).
"""
import unittest
from decimal import Decimal

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.pg_platform import PlatformBase
from backend.database.store_md_migration import (DETAIL_INDEX, STORE_MD_TABLES, migrate_store_md_schema,
                                                 pending_store_md_schema)
from backend.models_admin import Merchant, Store
from backend.models_admin.master_group import Category1, MdDetail, MdMaster
from backend.models_admin.store_md import StoreMd, StoreMdPrice


def sqlite_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda conn, _record: conn.execute("PRAGMA foreign_keys=ON"))
    return engine


def seed(db):
    db.add_all([Merchant(i_merchant_id=10, c_merchant_code="MER10", c_merchant_name="M10", c_status="active"),
                Merchant(i_merchant_id=20, c_merchant_code="MER20", c_merchant_name="M20", c_status="active")])
    db.flush()
    db.add_all([Store(i_store_id=100, c_store_name="A", i_merchant_id=10, c_status="active"),
                Store(i_store_id=101, c_store_name="B", i_merchant_id=10, c_status="active"),
                Store(i_store_id=200, c_store_name="C", i_merchant_id=20, c_status="active"),
                Category1(i_category1_id=1, c_category_code="CA100001", c_category_name="Food")])
    db.flush()
    for md_id, owner in ((1, 10), (2, 20)):
        db.add(MdMaster(i_md_id=md_id, i_merchant_id=owner, i_owner_key=owner, c_item_code=f"MD0000{md_id}",
                        c_item_name=f"Item {md_id}", c_item_kind="ITEM", i_category1_id=1))
    db.flush()
    db.add_all([MdDetail(i_md_detail_id=11, i_md_id=1, i_owner_key=10, i_detail_no=1, b_is_default=True, n_price=5),
                MdDetail(i_md_detail_id=12, i_md_id=1, i_owner_key=10, i_detail_no=2, n_price=9),
                MdDetail(i_md_detail_id=21, i_md_id=2, i_owner_key=20, i_detail_no=1, b_is_default=True, n_price=7)])
    db.commit()


class StoreMdConstraintTests(unittest.TestCase):
    def setUp(self):
        self.engine = sqlite_engine()
        self.addCleanup(self.engine.dispose)
        PlatformBase.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.db.close)
        seed(self.db)

    def refused(self, row):
        self.db.add(row)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def price(self, **kw):
        return StoreMdPrice(**{"i_store_id": 100, "i_md_id": 1, "i_md_detail_id": 11, "i_owner_key": 10,
                               "n_price": Decimal("4.50"), **kw})

    def test_price_rows_keep_store_md_and_detail_in_one_merchant(self):
        self.db.add_all([self.price(), self.price(i_store_id=101, n_price=Decimal("0.00")),  # $0.00 is valid
                         self.price(i_md_detail_id=12, n_price=Decimal("9.00"))])  # same as the Merchant price
        self.db.commit()
        row = self.db.query(StoreMdPrice).filter_by(i_store_id=100, i_md_detail_id=11).one()
        self.assertEqual((row.i_row_version, row.dt_created is not None, row.dt_updated), (1, True, None))
        cases = {
            "another Merchant's Store": self.price(i_store_id=200, i_md_detail_id=12),
            "another Merchant's MD (owner key of that MD)": self.price(i_md_id=2, i_md_detail_id=21, i_owner_key=20),
            "a Detail of another MD": self.price(i_store_id=101, i_md_detail_id=21),
            "owner key not the Store's Merchant": self.price(i_store_id=101, i_md_detail_id=12, i_owner_key=20),
            "negative price": self.price(i_store_id=101, i_md_detail_id=12, n_price=Decimal("-0.01")),
            "no price (NULL is never stored)": self.price(i_store_id=101, i_md_detail_id=12, n_price=None),
            "duplicate Store + Detail": self.price(n_price=Decimal("1.00")),
            "row version 0": self.price(i_store_id=101, i_md_detail_id=12, i_row_version=0),
        }
        for label, row in cases.items():
            with self.subTest(label):
                self.refused(row)
        self.assertEqual(self.db.query(StoreMdPrice).count(), 3)

    def test_pos_visibility_rows(self):
        self.db.add(StoreMd(i_store_id=100, i_md_id=1, i_owner_key=10, b_pos_visible=False))
        self.db.commit()
        self.db.add(StoreMd(i_store_id=101, i_md_id=1, i_owner_key=10))
        self.db.commit()
        self.assertTrue(self.db.query(StoreMd).filter_by(i_store_id=101).one().b_pos_visible)  # default visible
        for label, row in {
            "another Merchant's Store": StoreMd(i_store_id=200, i_md_id=1, i_owner_key=10),
            "another Merchant's MD": StoreMd(i_store_id=100, i_md_id=2, i_owner_key=10),
            "duplicate Store + MD": StoreMd(i_store_id=100, i_md_id=1, i_owner_key=10),
        }.items():
            with self.subTest(label):
                self.refused(row)

    def test_deleting_detail_md_or_store_removes_only_its_override_rows(self):
        self.db.add_all([self.price(), self.price(i_md_detail_id=12), self.price(i_store_id=101),
                         StoreMd(i_store_id=100, i_md_id=1, i_owner_key=10, b_pos_visible=False),
                         StoreMd(i_store_id=101, i_md_id=1, i_owner_key=10, b_pos_visible=False)])
        self.db.commit()
        keys = lambda: sorted((r.i_store_id, r.i_md_detail_id) for r in self.db.query(StoreMdPrice))  # noqa: E731
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM tb_md_detail WHERE i_md_detail_id = 12"))
        self.db.expire_all()
        self.assertEqual(keys(), [(100, 11), (101, 11)])
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM tb_store WHERE i_store_id = 101"))
        self.db.expire_all()
        self.assertEqual(keys(), [(100, 11)])
        self.assertEqual([r.i_store_id for r in self.db.query(StoreMd)], [100])
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM tb_md_master WHERE i_md_id = 1"))
        self.db.expire_all()
        self.assertEqual((self.db.query(StoreMdPrice).count(), self.db.query(StoreMd).count()), (0, 0))
        self.assertEqual(self.db.query(MdDetail).filter_by(i_md_id=2).count(), 1)  # the other Merchant is untouched

    def test_store_merchant_change_is_refused_while_overrides_exist(self):
        self.db.add(self.price())
        self.db.commit()
        with self.assertRaises(IntegrityError), self.engine.begin() as conn:
            conn.execute(text("UPDATE tb_store SET i_merchant_id = 20 WHERE i_store_id = 100"))
        with self.engine.begin() as conn:  # a Store without overrides can still change (same as before)
            conn.execute(text("UPDATE tb_store SET i_merchant_id = 20 WHERE i_store_id = 101"))


class StoreMdMigrationTests(unittest.TestCase):
    def existing_database(self):
        """An existing database from before SO-1a: every table except the new ones, no tb_md_detail index."""
        engine = sqlite_engine()
        self.addCleanup(engine.dispose)
        tables = [t for name, t in PlatformBase.metadata.tables.items() if name not in STORE_MD_TABLES]
        PlatformBase.metadata.create_all(engine, tables=tables)
        with engine.begin() as conn:
            conn.execute(text(f"DROP INDEX {DETAIL_INDEX}"))
        with sessionmaker(bind=engine)() as db:
            seed(db)
        return engine

    def snapshot(self, engine):
        with engine.connect() as conn:
            return {name: conn.execute(text(f"SELECT * FROM {name} ORDER BY 1")).all()
                    for name in ("tb_merchant", "tb_store", "tb_md_master", "tb_md_detail")}

    def test_migration_is_additive_and_repeatable(self):
        engine = self.existing_database()
        before = self.snapshot(engine)
        self.assertEqual(pending_store_md_schema(engine), {"tables": list(STORE_MD_TABLES), "detail_index": True})
        self.assertEqual(migrate_store_md_schema(engine),
                         {"created_tables": list(STORE_MD_TABLES), "detail_index_created": True})
        self.assertEqual(self.snapshot(engine), before)  # existing rows unchanged
        insp = inspect(engine)
        self.assertTrue(set(STORE_MD_TABLES) <= set(insp.get_table_names()))
        self.assertIn(DETAIL_INDEX, {ix["name"] for ix in insp.get_indexes("tb_md_detail")})
        self.assertEqual(pending_store_md_schema(engine), {"tables": [], "detail_index": False})
        self.assertEqual(migrate_store_md_schema(engine), {"created_tables": [], "detail_index_created": False})
        with sessionmaker(bind=engine)() as db:  # the composite FKs work on the migrated database
            db.add(StoreMdPrice(i_store_id=100, i_md_id=1, i_md_detail_id=11, i_owner_key=10, n_price=Decimal("1")))
            db.commit()
            db.add(StoreMdPrice(i_store_id=100, i_md_id=1, i_md_detail_id=21, i_owner_key=10, n_price=Decimal("1")))
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_new_database_is_left_to_the_generic_step(self):
        engine = sqlite_engine()
        self.addCleanup(engine.dispose)
        self.assertEqual(pending_store_md_schema(engine), {"tables": [], "detail_index": False})
        self.assertEqual(migrate_store_md_schema(engine), {"created_tables": [], "detail_index_created": False})
        self.assertEqual(inspect(engine).get_table_names(), [])
        PlatformBase.metadata.create_all(engine)  # the generic step: the index is part of the model
        self.assertIn(DETAIL_INDEX, {ix["name"] for ix in inspect(engine).get_indexes("tb_md_detail")})


if __name__ == "__main__":
    unittest.main()
