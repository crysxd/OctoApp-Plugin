from typing import Tuple

from octoapp.logging import LoggerLike
from octoapp.firebaseappstorage import FirebaseIdentityProvider
from octoapp.firebaseappstorage import PrinterIdUnavailableException
from octoapp.firebaseappstorage import sha256_urlsafe_base64, pbkdf2_key

from .elegooclient import ElegooClient


# Derives the Firebase identity from the Elegoo mainboard id and MAC. Both values
# are readable by the mobile app locally, so it can compute the same id and key.
class ElegooFirebaseIdentity(FirebaseIdentityProvider):

    def __init__(self, logger: LoggerLike):
        self.Logger = logger

    def GetPrinterId(self) -> str:
        mainboardId, mainboardMac = self._GetIdBaseValues()
        plainId = f"printer:{mainboardId}/{mainboardMac}"
        return sha256_urlsafe_base64(plainId.encode())

    def GetEncryptionKey(self) -> str:
        mainboardId, mainboardMac = self._GetIdBaseValues()
        return pbkdf2_key(mainboardId, mainboardMac)

    def _GetIdBaseValues(self) -> Tuple[str, str]:
        attributes = ElegooClient.Get().GetAttributes()
        if attributes is None or attributes.MainboardId is None or attributes.MainboardMac is None:
            raise PrinterIdUnavailableException("Failed to get attributes from Elegoo Client.")
        return (attributes.MainboardId, attributes.MainboardMac)
