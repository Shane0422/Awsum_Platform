"""Common Feature Registry / resolver (services.features): registry integrity, unchanged legacy results,
Merchant entitlement + Store assignment + availability + dependencies, and OFF -> ON restore without any write."""
import itertools
import unittest

from backend.models_admin.store import Store
from backend.routers import platform_store
from backend.services import features as F
from backend.services import store_item_features as items
from backend.services.store_locations import location_management_enabled

# Snapshots of the definitions before the registry (must stay identical).
OLD_STORE_OPTION_FIELDS = {
    "opt_customer": ("b_opt_customer", "customer_scope", "c_customer_scope"),
    "opt_time_attendance": ("b_opt_time_attendance", None, None),
    "opt_inventory": ("b_opt_inventory", None, None),
    "opt_location_management": ("b_opt_use_shelf_location", None, None),
    "opt_loyalty": ("b_opt_loyalty", "loyalty_scope", "c_loyalty_scope"),
    "opt_accounting": ("b_opt_accounting", None, None),
    "opt_use_color": ("b_opt_use_color", None, None),
    "opt_use_modifier": ("b_opt_use_modifier", None, None),
    "opt_use_bottle_deposit": ("b_opt_use_bottle_deposit", None, None),
    "opt_use_sugar_tax": ("b_opt_use_sugar_tax", None, None),
    "opt_use_output_device": ("b_opt_use_output_device", None, None),
}
OLD_ITEM_FEATURES = {
    "color": ("opt_use_color", "b_opt_use_color", False),
    "modifier": ("opt_use_modifier", "b_opt_use_modifier", True),
    "bottle_deposit": ("opt_use_bottle_deposit", "b_opt_use_bottle_deposit", False),
    "sugar_tax": ("opt_use_sugar_tax", "b_opt_use_sugar_tax", False),
    "output_device": ("opt_use_output_device", "b_opt_use_output_device", True),
}
OLD_ITEM_FEATURE_FIELDS = {"color": ("color_code_id",), "modifier": ("modifier",), "output_device": ("output_devices",)}


class NoWriteStore:
    """A Store stand-in that fails on any attribute write (the resolver must only read)."""

    def __init__(self, **values):
        object.__setattr__(self, "_values", values)

    def __getattr__(self, name):
        return self._values.get(name)

    def __setattr__(self, name, value):
        raise AssertionError(f"resolver wrote {name}")


def old_item_features(store=None):
    return {key: True if store is None else (default if getattr(store, col) is None else bool(getattr(store, col)))
            for key, (_payload, col, default) in OLD_ITEM_FEATURES.items()}


class FeatureRegistryTests(unittest.TestCase):
    def test_registry_is_the_single_definition_of_existing_options(self):
        self.assertEqual(platform_store.STORE_OPTION_FIELDS, OLD_STORE_OPTION_FIELDS)
        self.assertEqual(items.ITEM_FEATURES, OLD_ITEM_FEATURES)
        self.assertEqual(items.ITEM_FEATURE_FIELDS, OLD_ITEM_FEATURE_FIELDS)
        for payload_key, (column, _scope_key, scope_column) in OLD_STORE_OPTION_FIELDS.items():
            self.assertTrue(hasattr(Store, column), column)
            self.assertTrue(scope_column is None or hasattr(Store, scope_column), scope_column)
            self.assertEqual(items.store_option_default(payload_key), payload_key in ("opt_use_modifier", "opt_use_output_device"))
        self.assertFalse(items.store_option_default("opt_unknown"))

    def test_groups_tiers_and_coming_soon(self):
        self.assertEqual([f.key for f in F.features_in("business")],
                         ["customer", "loyalty", "gift_card", "time_attendance", "inventory", "location_management", "accounting"])
        self.assertEqual([f.key for f in F.features_in("item")], list(OLD_ITEM_FEATURES))
        self.assertEqual([f.key for f in F.features_in("channel")], ["kiosk", "online_ordering"])
        self.assertEqual(len(F.REGISTRY), len(F.FEATURES))
        self.assertTrue(all(F.feature(k).tier == F.BASE for k in OLD_ITEM_FEATURES))
        self.assertTrue(all(f.tier == F.ADDON for f in F.REGISTRY if f.group != "item"))
        self.assertEqual([f.key for f in F.REGISTRY if not f.available], ["gift_card"])
        # Channels: entitlement only; quantities / Device Licenses are judged by the license code.
        self.assertFalse(F.feature("kiosk").assignable or F.feature("online_ordering").assignable)
        self.assertEqual(set(F.REASON_TEXT), {F.NOT_AVAILABLE, F.NOT_CONTRACTED, F.MERCHANT_OFF, F.STORE_OFF, F.REQUIRES,
                                              F.STORE_INACTIVE})

    def test_registry_check_rejects_bad_definitions(self):
        good = F.FeatureDef("a", "A", "item", F.BASE)
        for bad in ((good, good), (F.FeatureDef("a", "A", "other", F.BASE),), (F.FeatureDef("a", "A", "item", "PRO"),),
                    (F.FeatureDef("a", "A", "item", F.BASE, requires=("missing",)),),
                    (F.FeatureDef("a", "A", "item", F.BASE, requires=("a",)),),
                    (F.FeatureDef("a", "A", "item", F.BASE, store_column="b_x"),)):
            with self.assertRaises(AssertionError):
                F._check_registry(bad)


class LegacyCompatibilityTests(unittest.TestCase):
    """entitlements=None (no Merchant table yet): results equal the code before the registry, for every value."""

    def test_item_features_equal_old_result_for_every_saved_combination(self):
        columns = [col for _p, col, _d in OLD_ITEM_FEATURES.values()]
        for values in itertools.product((None, True, False), repeat=len(columns)):
            store = Store(**dict(zip(columns, values)))
            self.assertEqual(items.item_features(store), old_item_features(store), values)
        self.assertEqual(items.item_features(), old_item_features())
        self.assertEqual(items.item_features(Store()), {"color": False, "modifier": True, "bottle_deposit": False,
                                                        "sugar_tax": False, "output_device": True})

    def test_location_management_and_store_option_values_unchanged(self):
        for value in (None, True, False):
            store = Store(b_opt_use_shelf_location=value)
            self.assertIs(location_management_enabled(store), bool(value))
            self.assertIs(items.store_option_enabled(store, "opt_location_management", "b_opt_use_shelf_location"), bool(value))
        self.assertIs(location_management_enabled(None), False)
        for payload_key, (column, _s, _c) in OLD_STORE_OPTION_FIELDS.items():
            for value in (None, True, False):
                expected = items.store_option_default(payload_key) if value is None else value
                self.assertIs(items.store_option_enabled(Store(**{column: value}), payload_key, column), expected)

    def test_legacy_entitlement_means_effective_equals_store_assignment(self):
        store = Store(b_opt_customer=True, b_opt_inventory=False, b_opt_use_color=None)
        states = F.resolve_features(store)
        self.assertTrue(all(s.entitled for s in states.values() if s.available))
        for key, state in states.items():
            if state.available and state.assignable:
                self.assertEqual(state.effective, state.assigned, key)
        self.assertEqual((states["customer"].assigned_source, states["color"].assigned_source), ("store", "default"))
        self.assertEqual((states["kiosk"].effective, states["kiosk"].assigned_source), (True, None))


class ResolverTests(unittest.TestCase):
    def test_entitlement_assignment_availability_and_reason(self):
        store = NoWriteStore(b_opt_customer=True, b_opt_loyalty=True, b_opt_inventory=False, b_opt_use_color=True)
        states = F.resolve_features(store, {"customer": True, "loyalty": False, "inventory": True})
        self.assertEqual((states["customer"].effective, states["customer"].reason), (True, None))
        self.assertEqual((states["loyalty"].effective, states["loyalty"].reason, states["loyalty"].assigned), (False, F.NOT_ENTITLED, True))
        self.assertEqual((states["inventory"].effective, states["inventory"].reason), (False, F.STORE_OFF))
        # Missing from the saved entitlements: tier default (BASE entitled, ADDON not).
        self.assertEqual((states["color"].entitled, states["color"].effective), (True, True))
        self.assertEqual((states["accounting"].entitled, states["accounting"].reason), (False, F.NOT_ENTITLED))
        self.assertEqual((states["kiosk"].entitled, states["kiosk"].reason), (False, F.NOT_ENTITLED))
        # Not built: Coming soon, whatever is saved.
        gift = F.resolve_features(store, {"gift_card": True})["gift_card"]
        self.assertEqual((gift.available, gift.entitled, gift.effective, gift.reason), (False, False, False, F.NOT_AVAILABLE))
        self.assertEqual(F.resolve_features(None)["gift_card"].reason, F.NOT_AVAILABLE)

    def test_base_features_can_be_turned_off_for_a_merchant(self):
        state = F.resolve_feature("modifier", Store(), {"modifier": False})
        self.assertEqual((state.assigned, state.assigned_source, state.effective, state.reason), (True, "default", False, F.NOT_ENTITLED))

    def test_merchant_level_ignores_store_assignment(self):
        states = F.resolve_features(None, {"inventory": True})
        self.assertEqual((states["inventory"].effective, states["inventory"].assigned_source), (True, None))
        self.assertEqual(states["customer"].reason, F.NOT_ENTITLED)

    def test_merchant_off_then_on_restores_each_store_without_any_write(self):
        stores = {"A": NoWriteStore(b_opt_inventory=True), "B": NoWriteStore(b_opt_inventory=False), "C": NoWriteStore()}
        effective = lambda ent: {n: F.feature_enabled("inventory", s, ent) for n, s in stores.items()}
        before = effective({"inventory": True})
        self.assertEqual(before, {"A": True, "B": False, "C": False})
        self.assertEqual(effective({"inventory": False}), {"A": False, "B": False, "C": False})
        off = F.resolve_feature("inventory", stores["A"], {"inventory": False})
        self.assertEqual((off.assigned, off.assigned_source, off.reason), (True, "store", F.NOT_ENTITLED))  # saved value still visible
        self.assertEqual(effective({"inventory": True}), before)
        self.assertEqual(stores["A"]._values, {"b_opt_inventory": True})  # nothing written (NoWriteStore would raise)

    def test_dependencies_block_and_report_the_missing_feature(self):
        registry = (
            F.FeatureDef("inventory", "Inventory", "business", F.ADDON, "b_opt_inventory", False, "opt_inventory"),
            F.FeatureDef("location_management", "Location", "business", F.ADDON, "b_opt_use_shelf_location", False,
                         "opt_location_management", requires=("inventory",)),
            F.FeatureDef("deep", "Deep", "business", F.ADDON, requires=("location_management",)),
        )
        F._check_registry(registry)
        store = NoWriteStore(b_opt_inventory=False, b_opt_use_shelf_location=True)
        ent = {"inventory": True, "location_management": True, "deep": True}
        states = F.resolve_features(store, ent, registry=registry)
        self.assertEqual((states["location_management"].effective, states["location_management"].reason,
                          states["location_management"].blocked_by), (False, F.REQUIRES, ("inventory",)))
        self.assertEqual(states["deep"].blocked_by, ("location_management",))
        # The feature's own reason wins over the dependency reason.
        own = F.resolve_features(store, {**ent, "location_management": False}, registry=registry)["location_management"]
        self.assertEqual((own.reason, own.blocked_by), (F.NOT_ENTITLED, ()))
        store_on = NoWriteStore(b_opt_inventory=True, b_opt_use_shelf_location=True)
        self.assertTrue(all(s.effective for s in F.resolve_features(store_on, ent, registry=registry).values()))

    def test_dependency_cycle_is_reported(self):
        registry = (F.FeatureDef("a", "A", "item", F.BASE, requires=("b",)), F.FeatureDef("b", "B", "item", F.BASE, requires=("a",)))
        with self.assertRaisesRegex(ValueError, "cycle"):
            F.resolve_features(None, None, registry=registry)

    def test_state_as_dict_for_screens_and_api(self):
        data = F.resolve_feature("loyalty", Store(b_opt_loyalty=True), {"loyalty": False}).as_dict()
        self.assertEqual(data, {"key": "loyalty", "label": "Loyalty / Points", "group": "business", "tier": F.ADDON,
                                "available": True, "contracted": False, "merchant_enabled": True, "merchant_source": None,
                                "entitled": False, "assignable": True, "assigned": True, "assigned_source": "store",
                                "store_active": True, "effective": False, "reason": F.NOT_CONTRACTED, "blocked_by": []})

    def test_wrappers_accept_saved_entitlements(self):
        store = Store(b_opt_use_shelf_location=True, b_opt_use_color=True)
        self.assertFalse(location_management_enabled(store, {"location_management": False}))
        self.assertTrue(location_management_enabled(store, {"location_management": True}))
        self.assertEqual(items.item_features(store, {"color": False})["color"], False)
        self.assertEqual(items.item_features(None, {"color": False}),
                         {"color": False, "modifier": True, "bottle_deposit": True, "sugar_tax": True, "output_device": True})


if __name__ == "__main__":
    unittest.main()
