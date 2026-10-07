import hashlib
import hmac
import json
from unittest.mock import Mock, patch

import pytest
import requests

from rotkehlchen.accounting.structures.balance import Balance
from rotkehlchen.constants.assets import A_BTC, A_ETH, A_USDC
from rotkehlchen.errors.misc import RemoteError
from rotkehlchen.exchanges.deribit import Deribit
from rotkehlchen.exchanges.manager import ExchangeManager
from rotkehlchen.fval import FVal
from rotkehlchen.types import ApiKey, ApiSecret, ExchangeAuthCredentials, Location


@pytest.fixture
def deribit(database, inquirer, function_scope_messages_aggregator) -> Deribit:
    return Deribit(
        name='deribit test', api_key=ApiKey('test-client'), secret=ApiSecret(b'test-secret'),
        database=database, msg_aggregator=function_scope_messages_aggregator,
    )


def test_deribit_registration(deribit: Deribit) -> None:
    manager = ExchangeManager(deribit.msg_aggregator)
    assert manager._get_exchange_module(Location.DERIBIT).Deribit is Deribit
    with deribit.db.conn.read_ctx() as cursor:
        assert cursor.execute('SELECT seq FROM location WHERE location=?', (Location.DERIBIT.serialize_for_db(),)).fetchone() == (67,)  # noqa: E501


def test_deribit_authentication_and_credential_edit(deribit: Deribit) -> None:
    response = Mock(status_code=200, text='{"result":{"summaries":[]}}')
    with patch.object(deribit.session, 'get', return_value=response) as query:
        assert deribit.validate_api_key() == (True, '')
        first_auth = query.call_args.kwargs['headers']['Authorization']
        deribit.edit_exchange_credentials(ExchangeAuthCredentials(
            api_key=ApiKey('replacement-client'), api_secret=ApiSecret(b'replacement-secret'),
            passphrase=None,
        ))
        assert deribit.validate_api_key() == (True, '')
    auth = query.call_args.kwargs['headers']['Authorization']
    params = dict(pair.split('=', 1) for pair in auth.removeprefix('deri-hmac-sha256 ').split(','))
    assert params['id'] == 'replacement-client'
    assert params['nonce'] not in first_auth
    payload = f'{params["ts"]}\n{params["nonce"]}\nGET\n/api/v2/private/get_account_summaries\n\n'
    assert params['sig'] == hmac.new(b'replacement-secret', payload.encode(), hashlib.sha256).hexdigest()  # noqa: E501
    assert query.call_args.args[0] == 'https://www.deribit.com/api/v2/private/get_account_summaries'


@pytest.mark.parametrize('should_mock_current_price_queries', [True])
def test_deribit_uses_equity_including_options_once(deribit: Deribit) -> None:
    with patch.object(deribit, '_query_account_summaries', return_value=[
        {'currency': 'BTC', 'balance': '1', 'equity': '1.25', 'options_value': '0.2'},
        {'currency': 'ETH', 'balance': '2', 'equity': '-0.5', 'options_value': '-2.5'},
        {'currency': 'USDC', 'equity': '10'},
        {'currency': 'USDT', 'equity': '0'},
    ]):
        balances, error = deribit.query_balances()
    assert error == ''
    assert balances == {
        A_BTC: Balance(amount=FVal('1.25'), value=FVal('1.875')),
        A_ETH: Balance(amount=FVal('-0.5'), value=FVal('-0.75')),
        A_USDC: Balance(amount=FVal(10), value=FVal(15)),
    }


@pytest.mark.parametrize('data', [[], {}, {'result': {}}, {'result': {'summaries': [None]}}, {'error': {'code': 13009, 'message': 'unauthorized'}}])  # noqa: E501
def test_deribit_rejects_bad_responses(deribit: Deribit, data) -> None:
    with patch.object(deribit.session, 'get', return_value=Mock(status_code=200, text=json.dumps(data))):  # noqa: E501
        valid, error = deribit.validate_api_key()
    assert valid is False
    assert error


@pytest.mark.parametrize('entries', [[{'currency': 'BTC'}], [{'currency': 'BTC', 'equity': 'bad'}], [{'currency': 'BTC', 'equity': '1'}] * 2])  # noqa: E501
def test_deribit_malformed_balance_does_not_return_partial_success(deribit, entries) -> None:
    with patch.object(deribit, '_query_account_summaries', return_value=entries):
        balances, error = deribit.query_balances()
    assert balances is None
    assert error


def test_deribit_connection_error_does_not_expose_credentials(deribit: Deribit) -> None:
    with patch.object(deribit.session, 'get', side_effect=requests.ConnectionError('test-secret')):
        assert deribit.validate_api_key() == (False, 'Could not connect to Deribit')


def test_deribit_preserves_equity_decimal_precision(deribit: Deribit) -> None:
    with patch.object(deribit.session, 'get', return_value=Mock(
        status_code=200,
        text='{"result":{"summaries":[{"currency":"BTC","equity":0.123456789123456789}]}}',
    )):
        assert deribit._query_account_summaries() == [
            {'currency': 'BTC', 'equity': '0.123456789123456789'},
        ]


def test_deribit_history_is_not_marked_as_imported(deribit: Deribit) -> None:
    with pytest.raises(RemoteError, match='balances only'):
        deribit.query_online_history_events(0, 1)
