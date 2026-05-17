from octoapp.firebaseappstorage import FirebaseIdentityProvider
from octoapp.firebaseappstorage import PrinterIdUnavailableException
from octoapp.firebaseappstorage import sha256_urlsafe_base64, pbkdf2_key

from linux_host.config import Config


# Derives the Firebase identity from the Bambu serial number and local access code.
# The serial is the PBKDF2 password, the access code the salt. Both are known to the
# mobile app after LAN pairing, so it can compute the same id and key.
class BambuFirebaseIdentity(FirebaseIdentityProvider):

    def __init__(self, config: Config):
        self.Config = config

    def GetPrinterId(self) -> str:
        serial, _ = self._GetIdBaseValues()
        return sha256_urlsafe_base64(f"printer:{serial}".encode())

    def GetEncryptionKey(self) -> str:
        serial, accessCode = self._GetIdBaseValues()
        return pbkdf2_key(password=serial, salt=accessCode)

    def _GetIdBaseValues(self):
        serial = self.Config.GetStr(Config.SectionBambu, Config.BambuPrinterSn, None)
        accessCode = self.Config.GetStr(Config.SectionBambu, Config.BambuAccessToken, None)
        if serial is None or accessCode is None:
            raise PrinterIdUnavailableException("Bambu serial number or access code not available.")
        return (serial, accessCode)
