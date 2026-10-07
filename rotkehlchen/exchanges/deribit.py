import json
from typing import TYPE_CHECKING, Any, Final
from uuid import uuid4

import requests

from rotkehlchen.assets.converters import asset_from_deribit
from rotkehlchen.constants.misc import ZERO
from rotkehlchen.db.settings import CachedSettings
from rotkehlchen.errors.asset import UnknownAsset, WrongAssetType
from rotkehlchen.errors.misc import RemoteError
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.exchanges.exchange import ExchangeInterface, ExchangeQueryBalances
from rotkehlchen.exchanges.utils import SignatureGeneratorMixin
from rotkehlchen.serialization.deserialize import deserialize_fval
from rotkehlchen.types import ApiKey, ApiSecret, Location
from rotkehlchen.utils.misc import ts_now_in_ms
from rotkehlchen.utils.mixins.cacheable import cache_response_timewise
from rotkehlchen.utils.mixins.lockable import protect_with_lock

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rotkehlchen.db.dbhandler import DBHandler
    from rotkehlchen.exchanges.data_structures import MarginPosition
    from rotkehlchen.history.events.structures.base import HistoryBaseEntry
    from rotkehlchen.types import Timestamp
    from rotkehlchen.user_messages import MessagesAggregator

DERIBIT_BASE_URL: Final = 'https://www.deribit.com'


class Deribit(ExchangeInterface, SignatureGeneratorMixin):
    """Read-only account equity integration, including the mark value of open options."""

    def __init__(
            self,
            name: str,
            api_key: ApiKey,
            secret: ApiSecret,
            database: DBHandler,
            msg_aggregator: MessagesAggregator,
    ) -> None:
        super().__init__(
            name=name,
            location=Location.DERIBIT,
            api_key=api_key,
            secret=secret,
            database=database,
            msg_aggregator=msg_aggregator,
        )

    def _query_account_summaries(self) -> list[dict[str, Any]]:
        path = '/api/v2/private/get_account_summaries'
        timestamp, nonce = str(ts_now_in_ms()), uuid4().hex
        signature = self.generate_hmac_signature(f'{timestamp}\n{nonce}\nGET\n{path}\n\n')
        try:
            response = self.session.get(
                f'{DERIBIT_BASE_URL}{path}',
                headers={'Authorization': (
                    f'deri-hmac-sha256 id={self.api_key},ts={timestamp},'
                    f'nonce={nonce},sig={signature}'
                )},
                timeout=CachedSettings().get_timeout_tuple(),
            )
        except requests.RequestException as e:
            raise RemoteError('Could not connect to Deribit') from e

        try:
            data = json.loads(response.text, parse_float=str)
        except json.JSONDecodeError as e:
            raise RemoteError('Deribit returned invalid JSON') from e

        if not isinstance(data, dict):
            raise RemoteError('Deribit returned an unexpected response')
        if (error := data.get('error')) is not None:
            if not isinstance(error, dict):
                raise RemoteError('Deribit returned an unexpected error response')
            raise RemoteError(
                f'Deribit API error {error.get("code", "unknown")}: '
                f'{error.get("message", "unknown error")}',
            )
        if response.status_code != 200:
            raise RemoteError(f'Deribit request failed with HTTP {response.status_code}')
        if (
                not isinstance(result := data.get('result'), dict) or
                not isinstance(summaries := result.get('summaries'), list) or
                not all(isinstance(entry, dict) for entry in summaries)
        ):
            raise RemoteError('Deribit returned invalid account summaries')
        return summaries

    def first_connection(self) -> None:
        self.first_connection_made = True

    def validate_api_key(self) -> tuple[bool, str]:
        try:
            self._query_account_summaries()
        except RemoteError as e:
            return False, str(e)
        return True, ''

    @protect_with_lock()
    @cache_response_timewise()
    def query_balances(self, **kwargs: Any) -> ExchangeQueryBalances:
        """Use per-currency equity; adding options_value again would double count it."""
        try:
            summaries = self._query_account_summaries()
            amounts = {}
            for entry in summaries:
                amount = deserialize_fval(entry['equity'])
                if amount == ZERO:
                    continue
                currency = entry['currency']
                if not isinstance(currency, str):
                    raise RemoteError('Deribit returned an invalid currency')
                try:
                    asset = asset_from_deribit(currency)
                except (UnknownAsset, WrongAssetType):
                    self.send_unknown_asset_message(currency, details='balance query')
                    continue
                if asset in amounts:
                    raise RemoteError('Deribit returned duplicate currency summaries')
                amounts[asset] = amount
        except (RemoteError, DeserializationError, KeyError) as e:
            return None, f'Could not query Deribit balances: {e!s}'
        return self.balances_from_amounts(amounts), ''

    def query_online_margin_history(
            self,
            start_ts: Timestamp,
            end_ts: Timestamp,
    ) -> list[MarginPosition]:
        return []

    def query_online_history_events(
            self,
            start_ts: Timestamp,
            end_ts: Timestamp,
            force_refresh: bool = False,
    ) -> tuple[Sequence[HistoryBaseEntry], Timestamp]:
        raise RemoteError('Deribit supports balances only; transaction history import is not supported')  # noqa: E501
