from typing import Tuple

from octoapp.logging import LoggerLike
from octoapp.firebaseappstorage import FirebaseIdentityProvider
from octoapp.firebaseappstorage import PrinterIdUnavailableException
from octoapp.firebaseappstorage import sha256_urlsafe_base64, pbkdf2_key

from .elegoocc2client import ElegooCc2Client


# Derives the Firebase identity from the CC2 serial number and MAC (falling back to
# IP, then a fixed salt). These values are readable by the mobile app locally, so it
# can compute the same id and key. Must stay in sync with the app's CC2 derivation.
class ElegooCc2FirebaseIdentity(FirebaseIdentityProvider):

    def __init__(self, logger: LoggerLike):
        self.Logger = logger

    def GetPrinterId(self) -> str:
        sn, second = self._GetIdBaseValues()
        plainId = f"printer:{sn}/{second}"
        return sha256_urlsafe_base64(plainId.encode())

    def GetEncryptionKey(self) -> str:
        sn, second = self._GetIdBaseValues()
        return pbkdf2_key(sn, second)

    def _GetIdBaseValues(self) -> Tuple[str, str]:
        attributes = ElegooCc2Client.Get().GetAttributes()
        if attributes is None or attributes.SerialNumber is None:
            raise PrinterIdUnavailableException("Failed to get attributes from Elegoo CC2 Client.")
        second = attributes.Mac or attributes.Ip or "salt_octoapp"
        return (attributes.SerialNumber, second)
