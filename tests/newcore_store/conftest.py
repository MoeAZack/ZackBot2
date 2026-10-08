"""NC02B_NO_DPAPI=1 simulates a platform without DPAPI (Linux CI) on any machine: DpapiCipher refuses to construct with
the same CipherUnavailable a non-Windows host raises, so every production default (cipher=None) fails closed there and
every real-file-system test must pick its cipher through nc02b_helpers.real_fs_cipher(). Cowork N6 (PR #43 round):
test_nc02b_no_dpapi.py runs the whole store suite this way from a Windows run."""
import os


def pytest_configure(config):
    if os.environ.get('NC02B_NO_DPAPI'):
        from newcore.store import cipher

        def _unavailable(self):
            raise cipher.CipherUnavailable(78, 'DPAPI exists on Windows only (NC02B_NO_DPAPI simulation)')
        cipher.DpapiCipher.__init__ = _unavailable
