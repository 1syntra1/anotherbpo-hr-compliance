"""Opt-in symmetric encryption for backups (shared by backup.py and restore.py).

Enabled only when a passphrase is supplied (HR_BACKUP_PASSPHRASE or --passphrase). HR
backups contain employee personal information, so at go-live they should be encrypted at
rest. Losing the passphrase makes the backups unrecoverable — store it with the same care
as the rest of the recovery credentials.

File format:  MAGIC(9) || salt(16) || Fernet token
Key:          PBKDF2-HMAC-SHA256(passphrase, salt, 200000) -> 32 bytes -> Fernet (AES-128-CBC + HMAC).
"""
import base64
import os

MAGIC = b"HRCS-ENC1"
_ITERATIONS = 200_000


def _key(passphrase, salt):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt_file(src, dst, passphrase):
    from cryptography.fernet import Fernet
    salt = os.urandom(16)
    with open(src, "rb") as fh:
        token = Fernet(_key(passphrase, salt)).encrypt(fh.read())
    with open(dst, "wb") as fh:
        fh.write(MAGIC)
        fh.write(salt)
        fh.write(token)


def is_encrypted(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def decrypt_file(src, dst, passphrase):
    from cryptography.fernet import Fernet, InvalidToken
    with open(src, "rb") as fh:
        if fh.read(len(MAGIC)) != MAGIC:
            raise ValueError("Not an HRCS encrypted backup.")
        salt = fh.read(16)
        token = fh.read()
    try:
        data = Fernet(_key(passphrase, salt)).decrypt(token)
    except InvalidToken:
        raise ValueError("Wrong passphrase, or the backup is corrupt.")
    with open(dst, "wb") as fh:
        fh.write(data)
