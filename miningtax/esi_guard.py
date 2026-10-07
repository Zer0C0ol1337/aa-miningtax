"""
Keeps ESI out of web requests.

The tax calculation the dashboard, pilot detail and CSV export run for every
ledger entry has ESI fallbacks deep inside it — an ore type no local source
knows, a corporation's alliance join date not stored yet, a character's corp
history Corptools doesn't have. In the nightly sync and Rebuild Snapshot those
fallbacks are wanted; in a page they would make the request wait on ESI.

Every view runs inside no_esi() (see check_access in views.py). The fallbacks
ask esi_allowed() first and, inside a request, return "not known yet" without
caching that as a final answer — the nightly sync and Rebuild Snapshot, which
run as tasks, fill the gap. A context variable rather than a parameter, so the
calculation's signature stays the same for every caller.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_esi_blocked = ContextVar('miningtax_esi_blocked', default=False)


@contextmanager
def no_esi():
    """Blocks the ESI fallbacks for the code run inside it (a web request)."""
    token = _esi_blocked.set(True)
    try:
        yield
    finally:
        _esi_blocked.reset(token)


def esi_allowed():
    """False inside no_esi() — the caller must not contact ESI."""
    return not _esi_blocked.get()
