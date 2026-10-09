"""Automatic backup policy using synthetic databases and temporary directories."""
import shutil
import tempfile
import zlib
from unittest.mock import Mock, patch

import pytest

from rotkehlchen.config import default_data_directory
from rotkehlchen.constants.misc import USERDB_NAME, USERSDIR_NAME
from rotkehlchen.data_handler import DataHandler
from rotkehlchen.data_migrations.migrations.migration_20 import data_migration_20
from rotkehlchen.data_migrations.progress import MigrationProgressHandler
from rotkehlchen.db.drivers.sqlite import DBConnection, DBConnectionType
from rotkehlchen.db.upgrade_manager import DBUpgradeManager
from rotkehlchen.db.utils import unlock_database
from rotkehlchen.errors.api import PremiumAuthenticationError
from rotkehlchen.errors.misc import DBUpgradeError, SystemPermissionError
from rotkehlchen.exchanges.data_structures import hash_id
from rotkehlchen.globaldb.upgrades.manager import _perform_single_upgrade
from rotkehlchen.globaldb.utils import initialize_globaldb
from rotkehlchen.premium.sync import PremiumSyncManager
from rotkehlchen.tests.data_migrations.test_migrations import MockRotkiForMigrations
from rotkehlchen.types import Location
from rotkehlchen.user_messages import MessagesAggregator
from rotkehlchen.utils.backups import auto_backups_enabled
from rotkehlchen.utils.datadir import _create_directory_with_potential_backup
from rotkehlchen.utils.upgrades import UpgradeRecord


@pytest.mark.parametrize('value', [None, '', '0', 'true', '01', '1'])
def test_explicit_backup_opt_out(monkeypatch, value):
    if value is None:
        monkeypatch.delenv('ROTKI_DISABLE_AUTO_BACKUPS', raising=False)
    else:
        monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', value)
    assert auto_backups_enabled() is (value != '1')


@pytest.mark.parametrize('disabled', [False, True])
@pytest.mark.parametrize('fails', [False, True])
def test_user_upgrade_backup_policy(database, monkeypatch, disabled, fails):
    monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', '1' if disabled else '0')
    with database.conn.read_ctx() as cursor:
        version = database.get_setting(cursor, 'version')

    def upgrade(db, progress_handler):
        with db.user_write() as cursor:
            cursor.execute('CREATE TABLE synthetic_upgrade(value TEXT)')
        if fails:
            raise RuntimeError('synthetic upgrade failure')

    with patch('rotkehlchen.db.upgrade_manager.shutil.copyfile', wraps=shutil.copyfile) as copy:
        manager = DBUpgradeManager(database)
        if fails:
            with pytest.raises(DBUpgradeError, match='synthetic upgrade failure') as error:
                manager._perform_single_upgrade(UpgradeRecord(version, upgrade), Mock())
            if disabled:
                assert 'no backup was created or restored' in str(error.value)
                assert isinstance(error.value.__cause__, RuntimeError)
        else:
            manager._perform_single_upgrade(UpgradeRecord(version, upgrade), Mock())
            with database.conn.read_ctx() as cursor:
                assert database.get_setting(cursor, 'version') == version + 1
                assert database.get_setting(cursor, 'ongoing_upgrade_from_version') is None
        assert copy.call_count == (0 if disabled else 2 if fails else 1)
    assert bool(list(database.user_data_dir.glob('*.backup'))) is not disabled
    if disabled and fails:
        with database.conn.read_ctx() as cursor:
            assert database.get_setting(cursor, 'ongoing_upgrade_from_version') == version


@pytest.mark.parametrize('disabled', [False, True])
@pytest.mark.parametrize('fails', [False, True])
def test_global_upgrade_backup_policy(tmp_path, monkeypatch, disabled, fails):
    monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', '1' if disabled else '0')
    connection = DBConnection(tmp_path / 'global.db', DBConnectionType.GLOBAL, 0)
    try:
        with connection.write_ctx() as cursor:
            cursor.executescript("CREATE TABLE settings(name TEXT PRIMARY KEY, value TEXT);"
                                 "INSERT INTO settings VALUES('version', '18');")

        def upgrade(connection, progress_handler):
            with connection.write_ctx() as cursor:
                cursor.execute('CREATE TABLE synthetic_upgrade(value TEXT)')
            if fails:
                raise RuntimeError('synthetic upgrade failure')

        with patch('rotkehlchen.globaldb.upgrades.manager.shutil.copyfile', wraps=shutil.copyfile) as copy:  # noqa: E501
            args = (UpgradeRecord(18, upgrade), connection, tmp_path, 'global.db', Mock())
            if fails:
                with pytest.raises(ValueError, match='synthetic upgrade failure') as error:
                    _perform_single_upgrade(*args)
                if disabled:
                    assert 'no backup was created or restored' in str(error.value)
                    assert isinstance(error.value.__cause__, RuntimeError)
            else:
                _perform_single_upgrade(*args)
                with connection.read_ctx() as cursor:
                    assert cursor.execute("SELECT value FROM settings WHERE name='version'").fetchone() == ('19',)  # noqa: E501
                    assert cursor.execute("SELECT value FROM settings WHERE name='ongoing_upgrade_from_version'").fetchone() is None  # noqa: E501
            assert copy.call_count == (0 if disabled else 2 if fails else 1)
        assert bool(list(tmp_path.glob('*.backup'))) is not disabled
    finally:
        connection.close()


@pytest.mark.parametrize('existing_backup', [False, True])
@pytest.mark.parametrize('resume', [False, True])
def test_disabled_user_upgrade_recovery(database, monkeypatch, existing_backup, resume):
    monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', '1')
    if existing_backup:
        (database.user_data_dir / '1_rotkehlchen_db_v53.backup').write_bytes(b'synthetic')
    with database.user_write() as cursor:
        database.set_setting(cursor, 'ongoing_upgrade_from_version', 53)
    with (
        patch('rotkehlchen.db.dbhandler.shutil.copyfile') as copy,
        pytest.raises(DBUpgradeError, match='no backup was restored'),
    ):
        database._check_unfinished_upgrades(resume_from_backup=resume)
    copy.assert_not_called()


@pytest.mark.parametrize('existing_backup', [False, True])
def test_disabled_global_upgrade_recovery(tmp_path, monkeypatch, existing_backup):
    monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', '1')
    connection = DBConnection(tmp_path / 'global.db', DBConnectionType.GLOBAL, 0)
    with connection.write_ctx() as cursor:
        cursor.executescript("CREATE TABLE settings(name TEXT PRIMARY KEY, value TEXT);"
                             "INSERT INTO settings VALUES('ongoing_upgrade_from_version', '18');")
    connection.close()
    if existing_backup:
        (tmp_path / '1_global_db_v18.backup').write_bytes(b'synthetic')
    with (
        patch('rotkehlchen.globaldb.utils.shutil.copyfile') as copy,
        pytest.raises(DBUpgradeError, match='no backup was restored'),
    ):
        initialize_globaldb(tmp_path, 'global.db', 0)
    copy.assert_not_called()


@pytest.mark.parametrize('disabled', [False, True])
def test_remote_import_backup_policy(database, monkeypatch, disabled):
    monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', '1' if disabled else '0')
    data = DataHandler(database.user_data_dir.parent.parent, MessagesAggregator(), 0)
    data.db = database
    data.username = database.user_data_dir.name
    with tempfile.NamedTemporaryFile() as temp:
        plaintext = database.export_unencrypted(temp).read_bytes()
    with (
        patch('rotkehlchen.data_handler.decrypt', return_value=zlib.compress(plaintext)),
        patch('rotkehlchen.data_handler.shutil.copyfile', wraps=shutil.copyfile) as copy,
        patch('rotkehlchen.db.dbhandler.shutil.copy2', wraps=shutil.copy2) as copy2,
    ):
        data.decompress_and_decrypt_db(b'synthetic encrypted payload')
    assert copy.call_count == (0 if disabled else 2)
    assert copy2.call_count == (0 if disabled else 1)
    assert bool(list(database.user_data_dir.glob('*.backup'))) is not disabled
    assert not (database.user_data_dir / 'rotkehlchen_temp_backup.db').exists()
    with database.conn.read_ctx() as cursor:
        assert database.get_setting(cursor, 'version') is not None


def test_explicit_backup_remains_available(database, monkeypatch):
    monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', '1')
    backup = database.create_db_backup()
    assert backup.is_file()
    assert backup.stat().st_size > 0


@pytest.mark.parametrize('disabled', [False, True])
def test_missing_database_login_backup(tmp_path, monkeypatch, disabled):
    monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', '1' if disabled else '0')
    user_dir = tmp_path / USERSDIR_NAME / 'synthetic'
    user_dir.mkdir(parents=True)
    (user_dir / 'marker').write_text('synthetic')
    data = DataHandler(tmp_path, MessagesAggregator(), 0)
    with patch('rotkehlchen.data_handler.shutil.move', wraps=shutil.move) as move:
        with pytest.raises(SystemPermissionError) as error:
            data.unlock('synthetic', 'synthetic', False, False)
        assert move.call_count == (0 if disabled else 1)
    if disabled:
        assert 'left in place' in str(error.value)
        assert (user_dir / 'marker').read_text() == 'synthetic'
    assert bool(list(user_dir.parent.glob('auto_backup_*'))) is not disabled


def test_disabled_legacy_directory_backup(tmp_path, monkeypatch):
    monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', '1')
    user_dir = tmp_path / USERSDIR_NAME
    user_dir.mkdir()
    (user_dir / USERDB_NAME).write_bytes(b'synthetic')
    with (
        patch('rotkehlchen.utils.datadir.copytree') as copy,
        pytest.raises(SystemPermissionError, match='manual relocation'),
    ):
        _create_directory_with_potential_backup(tmp_path, USERSDIR_NAME)
    copy.assert_not_called()
    assert (user_dir / USERDB_NAME).read_bytes() == b'synthetic'


def test_disabled_legacy_default_directory_copy(tmp_path, monkeypatch):
    monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', '1')
    old_dir = tmp_path / 'old'
    old_dir.mkdir()
    with (
        patch('rotkehlchen.config.old_data_directory', return_value=old_dir),
        patch('rotkehlchen.config.get_xdg_data_home', return_value=tmp_path / 'new'),
        patch('rotkehlchen.config.platform.system', return_value='Linux'),
        patch('rotkehlchen.config.shutil.copytree') as copy,
        pytest.raises(SystemPermissionError, match='manual relocation'),
    ):
        default_data_directory()
    copy.assert_not_called()


def test_disabled_periodic_premium_backup(database, monkeypatch):
    monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', '1')
    data = Mock(db=database)
    manager = PremiumSyncManager(Mock(), data)
    manager.premium = Mock()
    assert manager.check_if_should_sync(force_upload=False) is False
    assert manager.maybe_upload_data_to_server() == (False, None)
    manager.premium.get_capabilities.assert_not_called()
    data.compress_and_encrypt_db.assert_not_called()
    manager.last_upload_attempt_ts = 0
    manager.premium.get_capabilities.return_value = {'max_backup_size_mb': 100}
    assert manager.check_if_should_sync(force_upload=True) is True


def test_disabled_failed_premium_creation_backup(tmp_path, database, monkeypatch):

    monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', '1')
    data = Mock(db=database, user_data_dir=tmp_path, data_directory=tmp_path)
    manager = PremiumSyncManager(Mock(), data)
    with (
        patch('rotkehlchen.premium.sync.shutil.move') as move,
        pytest.raises(PremiumAuthenticationError, match='synthetic failure'),
    ):
        manager._abort_new_syncing_premium_user('synthetic', PremiumAuthenticationError('synthetic failure'))  # noqa: E501
    move.assert_not_called()
    data.logout.assert_called_once()


@pytest.mark.parametrize('disabled', [False, True])
def test_migration_trade_recovery_backup_policy(database, monkeypatch, disabled):

    monkeypatch.setenv('ROTKI_DISABLE_AUTO_BACKUPS', '1' if disabled else '0')
    backup_path = database.user_data_dir / '1_rotkehlchen_db_v47.backup'
    old = DBConnection(backup_path, DBConnectionType.USER, 0)
    try:
        unlock_database(old, database.password, database.sqlcipher_version)
        with old.write_ctx() as cursor:
            cursor.executescript(
                'CREATE TABLE trades(timestamp INTEGER, location TEXT, base_asset TEXT, '
                'quote_asset TEXT, type TEXT, amount TEXT, rate TEXT, fee TEXT, '
                'fee_currency TEXT, link TEXT, notes TEXT);'
                "INSERT INTO trades VALUES(1749566127, 'A', 'ETH', 'USD', 'A', '1', "
                "'100', NULL, NULL, '', 'synthetic trade');",
            )
    finally:
        old.close()
    broken_id = hash_id(str(Location.KRAKEN))
    with database.user_write() as cursor:
        cursor.execute(
            'INSERT INTO history_events(entry_type, group_identifier, sequence_index, timestamp, '
            'location, asset, amount, type, subtype) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (1, broken_id, 0, 1749566127000, 'A', 'ETH', '1', 'trade', 'spend'),
        )
    with (
        patch.object(database, 'create_db_backup', wraps=database.create_db_backup) as backup,
        patch('rotkehlchen.data_migrations.migrations.migration_20.shutil.move', wraps=shutil.move) as move,  # noqa: E501
    ):
        progress = MigrationProgressHandler(database.msg_aggregator, 20)
        progress.new_round(20)
        data_migration_20(MockRotkiForMigrations(database), progress)
    assert backup.call_count == (0 if disabled else 1)
    assert move.call_count == (0 if disabled else 1)
    assert bool(list(database.user_data_dir.glob('*_pre_recovery_v48.backup'))) is not disabled
    with database.conn.read_ctx() as cursor:
        assert cursor.execute('SELECT COUNT(*) FROM history_events WHERE group_identifier=?', (broken_id,)).fetchone()[0] == 0  # noqa: E501
        assert cursor.execute('SELECT asset, amount, subtype FROM history_events ORDER BY sequence_index').fetchall() == [  # noqa: E501
            ('USD', '100', 'spend'), ('ETH', '1', 'receive'),
        ]
