import json
import time
import threading
import uuid
from typing import List, Dict, Any

from octoapp.sentry import Sentry
from octoapp.logging import LoggerLike
from .moonrakerclient import MoonrakerClient
from .printernameprovider import IPrinterNameProvider

# Implements logic that deals with the moonraker database.
class MoonrakerDatabase(IPrinterNameProvider):

    def __init__(self, logger:LoggerLike,printerId:str, pluginVersion:str) -> None:
        self.PluginVersion = pluginVersion
        self.PresenceAnnouncementRunning = False
        self.PrinterId = printerId
        self.Logger = logger
        self.CachedEncryptionKey = None
        self._continuouslyAnnouncePresence()


    def GetAppsEntry(self) -> List[Dict[str,Any]]:
        self.Logger.debug("Getting apps")
        result = MoonrakerClient.Get().SendJsonRpcRequest("server.database.get_item",
        {
            "namespace": "octoapp",
            "key": "apps",
        })
        if result.GetErrorCode() == 404 or result.GetErrorCode() == -32601:
            self.Logger.error("No apps set")
            return []

        if result.HasError():
            self.Logger.error("Ensure database entry item post failed. "+result.GetLoggingErrorStr())
            raise Exception(f"Unable to fetch apps: {result.GetLoggingErrorStr()}")

        out:List[Dict[str,Any]] = []
        value = result.GetResult()["value"]
        for key in value.keys():
            out.append(value[key])
        return out

    def GetPrinterName(self) -> str:
        mainsailResult = MoonrakerClient.Get().SendJsonRpcRequest("server.database.get_item",
        {
            "namespace": "mainsail",
            "key": "general.printername",
        })
        if mainsailResult.HasError() is False and  mainsailResult.GetResult() is not None:
            return mainsailResult.GetResult()["value"]

        fluiddResult = MoonrakerClient.Get().SendJsonRpcRequest("server.database.get_item",
        {
            "namespace": "fluidd",
            "key": "uiSettings.general.instanceName",
        })
        if fluiddResult.HasError() is False and mainsailResult.GetResult() is not None:
            return mainsailResult.GetResult()["value"]

        if mainsailResult.HasError() is True and mainsailResult.GetErrorCode() != 404 and mainsailResult.GetErrorCode() != -3260:
            self.Logger.error("Failed to load Mainsail printer name"+mainsailResult.GetLoggingErrorStr())

        if fluiddResult.HasError() is True and fluiddResult.GetErrorCode() != 404 and fluiddResult.GetErrorCode() != -3260:
            self.Logger.error("Failed to load Fluidd printer name"+fluiddResult.GetLoggingErrorStr())

        return "Klipper"


    def GetOrCreateEncryptionKey(self):
        if self.CachedEncryptionKey is None:
            result = MoonrakerClient.Get().SendJsonRpcRequest("server.database.get_item",
            {
                "namespace": "octoapp",
                "key": "public.encryptionKey",
            })
            if result.HasError() is True and (result.GetErrorCode() == 404 or result.GetErrorCode() != -3260):
                self.Logger.warning("Encryption key not yet created")
            elif result.HasError() is False and result.GetResult() is not None:
                self.CachedEncryptionKey = result.GetResult()["value"]
            else:
                raise Exception(f"Failed to get encryption key {result.GetErrorStr()}")

        if self.CachedEncryptionKey is None:
            self.CachedEncryptionKey = str(uuid.uuid4())
            self.Logger.info("Created new encryption key")
            result = MoonrakerClient.Get().SendJsonRpcRequest("server.database.post_item",
            {
                "namespace": "octoapp",
                "key": "public.encryptionKey",
                "value": self.CachedEncryptionKey
            })
            if result.HasError() is True:
                # Just log. Should be flushed over time.
                self.Logger.error(f"Failed to set encryption key {result.GetErrorStr()}")

        return self.CachedEncryptionKey


    def RemoveAppEntries(self, apps:List[str]):
        self.Logger.info(f"Removing apps: {apps}")

        for appId in apps:
            result = MoonrakerClient.Get().SendJsonRpcRequest("server.database.delete_item",
            {
                "namespace": "octoapp",
                "key": f"apps.{appId}",
            })
            if result.HasError():
                self.Logger.error(f"Unable to remove app {appId}: {result.GetLoggingErrorStr()}")


    def EnsureOctoAppDatabaseEntry(self):
        # Useful for debugging.
        # self._Debug_EnumerateDataBase()

        # We use a few database entries under our own name space to share information with apps and other plugins.
        # Note that since these are used by 3rd party systems, they must never change. We also use this for our frontend.
        if self.PresenceAnnouncementRunning is False:
            self.PresenceAnnouncementRunning = True
            self._continuouslyAnnouncePresence()


    def _Debug_EnumerateDataBase(self):
        try:
            result = MoonrakerClient.Get().SendJsonRpcRequest("server.database.list")
            if result.HasError():
                self.Logger.error("_Debug_EnumerateDataBase failed to list. "+result.GetLoggingErrorStr())
                return
            nsList = result.GetResult()["namespaces"]
            for n in nsList:
                result = MoonrakerClient.Get().SendJsonRpcRequest("server.database.get_item",
                    {
                        "namespace": n
                    })
                if result.HasError():
                    self.Logger.error("_Debug_EnumerateDataBase failed to get items for "+n+". "+result.GetLoggingErrorStr())
                    return
                self.Logger.debug("Database namespace "+n+" : "+json.dumps(result.GetResult(), indent=4, separators=(", ", ": ")))
        except Exception as e:
            Sentry.ExceptionNoSend("_Debug_EnumerateDataBase exception.", e)

    def _continuouslyAnnouncePresence(self):
        t = threading.Thread(target=self._doContinuouslyAnnouncePresence)
        t.daemon = True
        t.start()

    def _doContinuouslyAnnouncePresence(self):
        self.Logger.info("Starting continuous update")
        while True:
            try:
                if MoonrakerClient.Get() is None:
                    self.Logger.info("Connection not ready...")
                    time.sleep(5)
                    continue

                self.Logger.info("Updating presence")
                result = MoonrakerClient.Get().SendJsonRpcRequest("server.database.post_item",
                {
                    "namespace": "octoapp",
                    "key": "public",
                    "value": {
                        "pluginVersion": self.PluginVersion,
                        "lastSeen": time.time(),
                        "printerId": self.PrinterId,
                        "encryptionKey": self.GetOrCreateEncryptionKey()
                    }
                })

                if result.HasError():
                    self.Logger.error("Ensure database entry item plugin version failed. "+result.GetLoggingErrorStr())
                    time.sleep(60)
                else:
                    time.sleep(300)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to update presence", e)
                time.sleep(30)
