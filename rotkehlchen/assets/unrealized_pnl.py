from typing import Final

from rotkehlchen.assets.asset import CustomAsset
from rotkehlchen.assets.types import AssetType
from rotkehlchen.globaldb.handler import GlobalDBHandler

GATE_PNL_ASSETS: Final = {
    'custom:gate:unrealized-pnl:BTC': 'BTC',
    'custom:gate:unrealized-pnl:USDT': 'eip155:1/erc20:0xdAC17F958D2ee523a2206206994597C13D831ec7',
}


def get_gate_pnl_asset(currency: str) -> CustomAsset:
    """Persist a separate PnL asset denominated in its contract's settlement currency."""
    identifier = f'custom:gate:unrealized-pnl:{currency}'
    if identifier not in GATE_PNL_ASSETS:
        raise ValueError(f'Unsupported Gate PnL settlement currency {currency}')

    asset = CustomAsset.initialize(
        identifier=identifier,
        name=f'Gate 浮盈 ({currency})',
        custom_asset_type='浮盈',
        notes=f'Unrealized futures profit or loss, denominated in {currency}. May be negative.',
    )
    with GlobalDBHandler().conn.write_ctx() as cursor:
        cursor.execute(
            'INSERT OR IGNORE INTO assets(identifier, name, type) VALUES (?, ?, ?)',
            (identifier, asset.name, AssetType.CUSTOM_ASSET.serialize_for_db()),
        )
        cursor.execute(
            'INSERT OR IGNORE INTO custom_assets(identifier, type, notes) VALUES (?, ?, ?)',
            asset.serialize_for_db(),
        )
    return asset
