"""Merchant feature layers, one row per Merchant and Feature Registry key (services.features).

- tb_merchant_feature = Platform BUSINESS OPTIONS (contract, Platform Admin only). No row = the registry tier default
  (BASE included, ADDON not). The 40 initial rows (c_source 'MIGRATION') were seeded once from the Stores' usage.
- tb_merchant_feature_choice = Merchant FEATURE ENTITLEMENTS (Merchant Admin's own ON/OFF inside the contract).
  No row = ON (follows the contract). Never changes the contract or billing.

Both: only their own layer. Store Options stay in tb_store.b_opt_* (never written from here); User Permission and
Device License are separate. Rows are never deleted (OFF = b_enabled false); keys not in the registry are ignored.
Read together through services.features.load_feature_snapshot (fail-closed).
"""
from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, UniqueConstraint

from backend.database.pg_platform import PlatformBase
from backend.models_common.audit_mixin import AuditMixin

SOURCE_MIGRATION = "MIGRATION"  # initial entitlement computed from the Stores' current usage
SOURCE_PLATFORM = "PLATFORM"    # set by Platform Admin


class MerchantFeature(PlatformBase, AuditMixin):
    """Platform Business Option (contract) of one Merchant."""
    __tablename__ = "tb_merchant_feature"
    __table_args__ = (UniqueConstraint("i_merchant_id", "c_feature_key", name="uq_tb_merchant_feature_merchant_key"),)

    i_merchant_feature_id = Column(Integer, primary_key=True, autoincrement=True)
    i_merchant_id = Column(Integer, ForeignKey("tb_merchant.i_merchant_id"), nullable=False, index=True)
    c_feature_key = Column(String(50), nullable=False)
    b_enabled = Column(Boolean, nullable=False)
    c_source = Column(String(20), nullable=False, default=SOURCE_PLATFORM)


class MerchantFeatureChoice(PlatformBase, AuditMixin):
    """Merchant Feature Entitlement (the Merchant's own choice inside its contract). Starts empty: no row = ON."""
    __tablename__ = "tb_merchant_feature_choice"
    __table_args__ = (UniqueConstraint("i_merchant_id", "c_feature_key", name="uq_tb_merchant_feature_choice_merchant_key"),)

    i_merchant_feature_choice_id = Column(Integer, primary_key=True, autoincrement=True)
    i_merchant_id = Column(Integer, ForeignKey("tb_merchant.i_merchant_id"), nullable=False, index=True)
    c_feature_key = Column(String(50), nullable=False)
    b_enabled = Column(Boolean, nullable=False)
