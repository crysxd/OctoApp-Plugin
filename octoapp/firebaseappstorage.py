import hashlib
import base64
import json
import atexit
import signal
import time
import threading
from abc import abstractmethod
from typing import List, Any, Dict, Optional, Set

import requests
from Crypto.Cipher import AES

from .logging import LoggerLike
from .appsstorage import AppInstance
from .appsstorage import AppStoragePlatformHelper


def sha256_urlsafe_base64(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode()


def pbkdf2_key(password: str, salt: str) -> str:
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100_000)
    return base64.b64encode(key).decode()


class PrinterIdUnavailableException(Exception):
    pass


# Platform-specific identity. Both methods may raise PrinterIdUnavailableException
# if the underlying printer values are not available yet.
class FirebaseIdentityProvider:

    # Stable per-printer id used as the Firebase database key. Must match the value
    # the mobile app computes independently.
    @abstractmethod
    def GetPrinterId(self) -> str:
        raise NotImplementedError()

    # Shared encryption key (base64). Must match the value the mobile app computes
    # independently.
    @abstractmethod
    def GetEncryptionKey(self) -> str:
        raise NotImplementedError()


# Generic Firebase Realtime Database app storage. Apps are written by the mobile
# app, keyed by the printer id, and AES-256-GCM encrypted with the shared key.
# Also announces presence to a second Firebase database.
class FirebaseAppStorage(AppStoragePlatformHelper):

    # One shared Firebase project for all printer platforms.
    AppsDatabaseUrl = "https://octoapp-companion-connections-apps.europe-west1.firebasedatabase.app"
    PresenceDatabaseUrl = "https://octoapp-companion-connections.europe-west1.firebasedatabase.app"

    # Apps are read very frequently (the activity expiry loop alone asks twice a minute, and every
    # notification asks again) but they rarely change. Reading the whole node every time was dominating
    # our Firebase download bill, so serve a cached copy and go back to Firebase in two steps.

    # How long a full app list stays usable before every value is downloaded again. This only matters
    # for changes that keep the same key, i.e. an app renewing its record under a new expiry date, so
    # it can be long.
    AppsCacheTtlSec = 3600

    # How often we ask Firebase for the key set only. A new registration or a new live activity token
    # always appears as a new key, because the key is a hash of the instance id and the FCM token, so
    # this is what decides how quickly those get picked up. A shallow read returns "true" in place of
    # every value, so it is a small fraction of the size of a full read.
    AppsProbeIntervalSec = 120

    def __init__(self, logger: LoggerLike, pluginVersion: str, identityProvider: FirebaseIdentityProvider):
        self.PluginVersion = pluginVersion
        self.First = False
        self.Logger = logger
        self.IdentityProvider = identityProvider
        self._AppsCacheLock = threading.Lock()
        self._AppsCache: Optional[List[AppInstance]] = None
        self._AppsCacheAt = 0.0
        self._AppsCacheKeys: Set[str] = set()
        self._AppsProbedAt = 0.0
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
            printerId = self.IdentityProvider.GetPrinterId()
            url = f"{self.PresenceDatabaseUrl}/{printerId}.json?print=silent"
            data = {
                "active": "false",
                "lastSeen": int(time.time()),
                "version": self.PluginVersion,
            }
            resp = requests.put(url, json=data, timeout=10)
            if resp.status_code > 299:
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
        printerId = self.IdentityProvider.GetPrinterId()
        url = f"{self.PresenceDatabaseUrl}/{printerId}.json?print=silent"
        data = {
            "active": "true",
            "lastSeen": int(time.time()),
            "version": self.PluginVersion,
        }
        resp = requests.put(url, json=data, timeout=10)

        if resp.status_code > 299:
            self.Logger.error(f"Failed to announce presence: {resp.status_code} {resp.text}")
        else:
            self.Logger.info(f"Announced presence for printer {printerId}")

    def GetAllApps(self) -> List[AppInstance]:
        now = time.time()
        with self._AppsCacheLock:
            cached = self._AppsCache
            knownKeys = self._AppsCacheKeys
            cacheAge = now - self._AppsCacheAt
            probeAge = now - self._AppsProbedAt

        if cached is not None and cacheAge < self.AppsCacheTtlSec:
            if probeAge < self.AppsProbeIntervalSec:
                return list(cached)

            # Cheap check for anything added or removed. Only a changed key set makes us download all
            # the values again.
            try:
                keys = self._FetchAppKeys()
            except Exception as e:
                self.Logger.error(f"Failed to probe apps, serving cached list: {str(e)}")
                with self._AppsCacheLock:
                    self._AppsProbedAt = time.time()
                return list(cached)

            if keys == knownKeys:
                with self._AppsCacheLock:
                    self._AppsProbedAt = time.time()
                return list(cached)

            self.Logger.info(f"App keys changed ({len(knownKeys)} -> {len(keys)}), reloading apps")

        # Fetch outside the lock, this does network IO and must not block other callers.
        try:
            apps, keys = self._FetchAllApps()
        except Exception as e:
            # Never cache a failure. Caching an empty list here would silently stop every
            # notification for a whole TTL after a single transient Firebase error, so keep serving
            # the previous list and retry on the next call instead.
            self.Logger.error(f"Failed to fetch apps, serving {'cached' if cached is not None else 'empty'} list: {str(e)}")
            return list(cached) if cached is not None else []

        with self._AppsCacheLock:
            self._AppsCache = apps
            self._AppsCacheKeys = keys
            self._AppsCacheAt = time.time()
            self._AppsProbedAt = time.time()

        return list(apps)

    # Drops the cached app list so the next read goes back to Firebase. Call after anything that
    # changes what is stored, otherwise we would keep serving apps we just deleted.
    def _InvalidateAppsCache(self):
        with self._AppsCacheLock:
            self._AppsCache = None
            self._AppsCacheKeys = set()
            self._AppsCacheAt = 0.0
            self._AppsProbedAt = 0.0

    # Reads the app keys without their values. Firebase replaces every value with "true" for a shallow
    # read, so this stays small no matter how many apps are registered.
    def _FetchAppKeys(self) -> Set[str]:
        printerId = self.IdentityProvider.GetPrinterId()
        url = f"{self.AppsDatabaseUrl}/{printerId}.json?shallow=true"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            raise Exception(f"Failed to probe apps: {resp.status_code} {resp.text}")

        data = resp.json()
        if data is None:
            # No node for this printer, so no apps are registered.
            return set()
        if not isinstance(data, dict):
            raise Exception(f"Unexpected shallow response for apps: {resp.text}")

        return set(data.keys())

    def _FetchAllApps(self) -> "tuple[List[AppInstance], Set[str]]":
        printerId = self.IdentityProvider.GetPrinterId()
        url = f"{self.AppsDatabaseUrl}/{printerId}.json"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            raise Exception(f"Failed to fetch apps: {resp.status_code} {resp.text}")

        data = resp.json()
        if data is None:
            # No node for this printer, so no apps are registered. That is a valid result and safe
            # to cache, unlike a request failure.
            return [], set()

        apps: List[AppInstance] = []
        for appId, encryptedAppJson in data.items():
            try:
                decryptedJson = self._DecryptAppInstance(appId, encryptedAppJson)
                appInstance = AppInstance.FromDict(decryptedJson, databaseId=appId)
                apps.append(appInstance)
            except Exception as e:
                self.Logger.error(f"Failed to decrypt or parse app {appId}: {str(e)}")

        # Track every key we saw, including ones we failed to decrypt, so a broken record does not
        # look like a change on every probe and trigger a full reload each time.
        return apps, set(data.keys())

    def RemoveApps(self, apps: List[AppInstance]):
        printerId = self.IdentityProvider.GetPrinterId()
        try:
            for app in apps:
                instanceId = app.InstanceId
                appId = app.DatabaseId or sha256_urlsafe_base64(instanceId.encode())
                url = f"{self.AppsDatabaseUrl}/{printerId}/{appId}.json?print=silent"
                resp = requests.delete(url, timeout=10)
                if resp.status_code != 200:
                    self.Logger.error(f"Failed to remove app {instanceId}: {resp.status_code} {resp.text}")
        finally:
            # Invalidate even on failure, we no longer know which deletes went through.
            self._InvalidateAppsCache()

    def GetOrCreateEncryptionKey(self) -> str:
        return self.IdentityProvider.GetEncryptionKey()

    def _DecryptAppInstance(self, appId: str, encryptedBase64: str) -> Dict[str, Any]:
        encryptionKey = self.GetOrCreateEncryptionKey()
        key_bytes = base64.urlsafe_b64decode(encryptionKey)

        # Decode and pad base64 if needed
        padded = encryptedBase64 + '=' * (-len(encryptedBase64) % 4)
        encrypted = base64.urlsafe_b64decode(padded)

        # Split ciphertext and tag (GCM tag is 16 bytes at the end). Remove the first 12 bytes for IV.
        if len(encrypted) < (16 + 12):
            raise ValueError("Encrypted data too short for GCM tag")
        ciphertext = encrypted[12:-16]
        iv = encrypted[:12]
        tag = encrypted[-16:]

        cipher = AES.new(key_bytes, AES.MODE_GCM, nonce=iv)  # type:ignore
        decrypted_bytes = cipher.decrypt_and_verify(ciphertext, tag)

        decrypted_json = decrypted_bytes.decode('utf-8')
        return json.loads(decrypted_json)
