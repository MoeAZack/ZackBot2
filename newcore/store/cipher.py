"""Evidence ciphers (nc02_design.md 8.2, decision D5).

Production: `DpapiCipher` = Windows DPAPI in the CurrentUser scope through ctypes (crypt32 CryptProtectData /
CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN), with entropy b'zackbot/nc02/evidence/v1|' + account_id. Stdlib only, no
key file to lose or leak; other local users cannot decrypt. Off Windows there is no stdlib authenticated cipher, so
`default_cipher()` raises CipherUnavailable and an evidence copy fails closed (the account stays HOLD, the original stays
untouched); the AES-GCM fallback of the design needs the `cryptography` package (Codex call, see the NC-02b report).

`InsecureTestCipher` exists for in-memory drills only: deterministic and NOT secret. It refuses to construct unless
`test_only=True` is passed explicitly, and the production entry points never construct it.
"""
from __future__ import annotations

import hashlib
import sys

DESCRIPTION = 'ZackBot NC-02 evidence'


class CipherUnavailable(OSError):
    """No usable evidence cipher on this platform: the evidence copy fails, the account stays HOLD."""


def entropy_for(account_id):
    return b'zackbot/nc02/evidence/v1|' + account_id.encode('ascii')


class DpapiCipher:
    name = 'dpapi-user-v1'

    def __init__(self):
        if sys.platform != 'win32':
            raise CipherUnavailable(78, 'DPAPI exists on Windows only')

    @staticmethod
    def _api():
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.LocalFree.argtypes = [ctypes.c_void_p]
        sig = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
               wintypes.DWORD, ctypes.POINTER(Blob)]
        crypt32.CryptProtectData.argtypes = sig
        crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p] + sig[2:]
        return ctypes, Blob, crypt32, k32

    def _call(self, fn_name, data, entropy, desc):
        ctypes, Blob, crypt32, k32 = self._api()
        buf_in = ctypes.create_string_buffer(bytes(data), len(data))
        buf_ent = ctypes.create_string_buffer(entropy, len(entropy))
        blob_in = Blob(len(data), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
        blob_ent = Blob(len(entropy), ctypes.cast(buf_ent, ctypes.POINTER(ctypes.c_char)))
        out = Blob()
        fn = getattr(crypt32, fn_name)
        ok = fn(ctypes.byref(blob_in), desc, ctypes.byref(blob_ent), None, None, 0x1, ctypes.byref(out))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            k32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def seal(self, plaintext, account_id):
        return self._call('CryptProtectData', plaintext, entropy_for(account_id), DESCRIPTION)

    def open(self, ciphertext, account_id):
        return self._call('CryptUnprotectData', ciphertext, entropy_for(account_id), None)


class InsecureTestCipher:
    """Deterministic keystream XOR for in-memory drills. Not a cipher: never used outside tests."""
    name = 'test-only-v1'

    def __init__(self, *, test_only=False):
        if test_only is not True:
            raise CipherUnavailable(1, 'InsecureTestCipher is for tests only')

    @staticmethod
    def _stream(n, account_id):
        out, i = bytearray(), 0
        while len(out) < n:
            out += hashlib.sha256(entropy_for(account_id) + i.to_bytes(8, 'big')).digest()
            i += 1
        return bytes(out[:n])

    def seal(self, plaintext, account_id):
        return bytes(a ^ b for a, b in zip(plaintext, self._stream(len(plaintext), account_id)))

    open = seal


def default_cipher():
    """The production cipher of this platform (DPAPI), or CipherUnavailable."""
    return DpapiCipher()
