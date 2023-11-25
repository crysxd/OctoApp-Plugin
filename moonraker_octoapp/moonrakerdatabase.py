import json
import logging

from octoapp.sentry import Sentry
from octoapp.appsstorage import AppInstance

from .moonrakerclient import MoonrakerClient

# Implements logic that deals with the moonraker database.
class MoonrakerDatabase:

    def __init__(self, logger:logging.Logger, printerId:str, pluginVersion:str) -> None:
        self.Logger = logger
        self.PrinterId = printerId
        self.PluginVersion = pluginVersion

    def GetAppsEntry(self):
        self.Logger.info("Getting apps")
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
            raise Exception("Unable to fetch apps: %s" % result.GetLoggingErrorStr())

        out = []
        value = result.GetResult()["value"]
        for key in value.keys(): out.append(value[key])
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

        if mainsailResult.HasError() is False & mainsailResult.GetErrorCode() != 404 or mainsailResult.GetErrorCode() != -3260:
            self.Logger.error("Failed to load Mainsail printer name"+mainsailResult.GetLoggingErrorStr())

        if fluiddResult.HasError() is False & fluiddResult.GetErrorCode() != 404 or fluiddResult.GetErrorCode() != -3260:
            self.Logger.error("Failed to load Fluidd printer name"+fluiddResult.GetLoggingErrorStr())

        return "Klipper"

    def RemoveAppEntries(self, apps: []):
        self.Logger.info("Removing apps: %s" % apps)

        for appId in apps:
            result = MoonrakerClient.Get().SendJsonRpcRequest("server.database.delete_item",
            {
                "namespace": "octoapp",
                "key": "apps.%s" % appId,
            })
            if result.HasError():
                self.Logger.error("Unable to remove app %s: %s" % (appId, result.GetLoggingErrorStr()))


    def EnsureOctoAppDatabaseEntry(self):
        # Useful for debugging.
        # self._Debug_EnumerateDataBase()

        # We use a few database entries under our own name space to share information with apps and other plugins.
        # Note that since these are used by 3rd party systems, they must never change. We also use this for our frontend.
        result = MoonrakerClient.Get().SendJsonRpcRequest("server.database.post_item",
        {
            "namespace": "octoapp",
            "key": "public.pluginVersion",
            "value": self.PluginVersion
        })
        if result.HasError():
            self.Logger.error("Ensure database entry item plugin version failed. "+result.GetLoggingErrorStr())
            return
        self.Logger.debug("Ensure database items posted successfully.")


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
            Sentry.Exception("_Debug_EnumerateDataBase exception.", e)
