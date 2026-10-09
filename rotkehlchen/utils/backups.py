import os


def auto_backups_enabled() -> bool:
    """Allow automatic persistent database/data backups unless explicitly disabled.

    Only ROTKI_DISABLE_AUTO_BACKUPS=1 opts out. Explicit user-requested backups
    and temporary working databases are unaffected.
    """
    return os.environ.get('ROTKI_DISABLE_AUTO_BACKUPS') != '1'
