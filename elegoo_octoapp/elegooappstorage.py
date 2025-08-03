import hashlib
import base64
import json
import atexit
import signal
import requests
import time
import threading
from typing import List, Tuple, Any, Dict

from Crypto.Cipher import AES

from octoapp.logging import LoggerLike
from octoapp.appsstorage import AppInstance
from octoapp.appsstorage import AppStoragePlatformHelper

from .elegooclient import ElegooClient

def sha256_urlsafe_base64(data: bytes) -> str:
    b64 = base64.b64encode(hashlib.sha256(data).digest()).decode()
    return b64.replace('/', '_').replace('+', '-')

def pbkdf2_key(password: str, salt: str) -> str:
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100_000)
    return base64.urlsafe_b64encode(key).rstrip(b'=').decode()

class ElegooAppStorage(AppStoragePlatformHelper):

    def __init__(self, logger:LoggerLike, client:ElegooClient, pluginVersion:str):
        self.PluginVersion = pluginVersion
        self.First = False
        self.Logger = logger
        self.Client = client
        self.DatabaseUrl = "https://octoapp-companion-connections-apps.europe-west1.firebasedatabase.app"
        self._ContinuouslyAnnouncePresence()
        self._RegisterLastWillHandler()

    def _RegisterLastWillHandler(self):
        # Register cleanup for normal exit
        atexit.register(self._SendLastWillInactive)
        # Register cleanup for SIGTERM and SIGINT
        signal.signal(signal.SIGTERM, lambda signum, frame: self._SendLastWillInactive())
        signal.signal(signal.SIGINT, lambda signum, frame: self._SendLastWillInactive())

    def _SendLastWillInactive(self):
        try:
            printerId = self._GetPrinterId()
            url = f"{self.DatabaseUrl}/{printerId}.json"
            data = {
                "active": "false",
                "lastSeen": int(time.time()),
                "version": self.PluginVersion,
            }
            resp = requests.put(url, json=data)
            if resp.status_code != 200:
                self.Logger.error(f"Failed to send last will: {resp.status_code} {resp.text}")
            else:
                self.Logger.info(f"Sent last will (inactive) for printer {printerId}")
        except PrinterIdUnavailableException as e:
            self.Logger.error(f"Unable to send last will, printer id not available: {str(e)}")
        except Exception as e:
            self.Logger.error(f"Exception in last will handler: {str(e)}")

    def _ContinuouslyAnnouncePresence(self):
        t = threading.Thread(target=self._DoContinuouslyAnnouncePresence)
        t.daemon = True
        t.start()

    def _DoContinuouslyAnnouncePresence(self):
        while True:
            try:
                self._AnnouncePresence()
                time.sleep(1800)
            except PrinterIdUnavailableException as e:
                self.Logger.error(f"Cannot announce presence: {str(e)}")
                time.sleep(5)
            except Exception as e:
                self.Logger.error(f"Failed to announce presence: {str(e)}")
                time.sleep(60)

    def _AnnouncePresence(self):
        printerId = self._GetPrinterId()
        url = f"{self.DatabaseUrl}/{printerId}.json"
        data = {
            "active": "true",
            "lastSeen": int(time.time()),
            "version": self.PluginVersion,
        }
        resp = requests.put(url, json=data)

        if resp.status_code != 200:
            self.Logger.error(f"Failed to announce presence: {resp.status_code} {resp.text}")
        else:
            self.Logger.info(f"Announced presence for printer {printerId}")

    def GetAllApps(self) -> List[AppInstance]:
        printerId = self._GetPrinterId()
        url = f"{self.DatabaseUrl}/{printerId}.json"
        resp = requests.get(url)
        if resp.status_code != 200:
            self.Logger.error(f"Failed to fetch apps: {resp.status_code} {resp.text}")
            return []
        
        data = resp.json()
        if data is None:
            return []

        apps:List[AppInstance] = []
        for appId, encryptedAppJson in data.items():
            try:
                # Decrypt app instance (you need to implement AES-256-GCM decryption separately)
                decryptedJson = self._DecryptAppInstance(appId, encryptedAppJson)
                appInstance = AppInstance.FromDict(decryptedJson)
                apps.append(appInstance)
            except Exception as e:
                self.Logger.error(f"Failed to decrypt or parse app {appId}: {str(e)}")
        return apps

    def RemoveApps(self, apps: List[AppInstance]):
        printerId = self._GetPrinterId()
        for app in apps:
            instanceId = app.InstanceId
            appId = sha256_urlsafe_base64(instanceId.encode())
            url = f"{self.DatabaseUrl}/{printerId}/{appId}.json"
            resp = requests.delete(url)
            if resp.status_code != 200:
                self.Logger.error(f"Failed to remove app {instanceId}: {resp.status_code} {resp.text}")


    def GetOrCreateEncryptionKey(self) -> str:
        mainboardId, mainboardMac = self._GetIdBaseValues()
        return pbkdf2_key(mainboardId, mainboardMac)

    def _GetIdBaseValues(self) -> Tuple[str, str]:
        attributes = self.Client.GetAttributes() 
        if attributes is None or attributes.MainboardId is None or attributes.MainboardMac is None:
            raise PrinterIdUnavailableException("Failed to get attributes from Elegoo Client.")
        return (attributes.MainboardId, attributes.MainboardMac)

    def _GetPrinterId(self) -> str:
        mainboardId, mainboardMac = self._GetIdBaseValues()
        plainId = f"printer:{mainboardId}/{mainboardMac}"
        printerId = sha256_urlsafe_base64(plainId.encode())
        return printerId

    def _DecryptAppInstance(self, appId: str, encryptedBase64: str) -> Dict[str, Any]:
        encryptionKey = self.GetOrCreateEncryptionKey()
        key_bytes = base64.urlsafe_b64decode(encryptionKey + '==')  # pad if needed

        printerId = self._GetPrinterId()
        iv = printerId.encode()[:12]  # first 12 bytes of printerId as IV

        encrypted_bytes = base64.urlsafe_b64decode(encryptedBase64 + '==')  # pad if needed
        
        # AES-GCM ciphertext includes the tag at the end (last 16 bytes)
        ciphertext = encrypted_bytes[:-16]
        tag = encrypted_bytes[-16:]

        cipher = AES.new(key_bytes, AES.MODE_GCM, nonce=iv)  # type: ignore
        decrypted_bytes = cipher.decrypt_and_verify(ciphertext, tag)

        decrypted_json = decrypted_bytes.decode('utf-8')
        return json.loads(decrypted_json)
    
class PrinterIdUnavailableException(Exception):
    pass