
import os
import json
from .subplugin import OctoAppSubPlugin
from octoapp.sentry import Sentry
from octoapp.appsstorage import AppInstance

class OctoPrintAppStorageSubPlugin(OctoAppSubPlugin):

    def __init__(self, parent):
        super().__init__(parent)

    def on_after_startup(self):
        self.UpgradeDataStructure()
        self.UpgradeExpirationDate()

    # !! Platform Command Handler Interface Function !!
    #
    # This must return a list of AppInstance
    #
    def GetAllApps(self) -> [AppInstance]:
        try: 
            if os.path.isfile(self.data_file):
                with open(self.data_file, 'r') as file:
                    apps = json.load(file)
                if apps is None:
                    apps = []
                
                return list(map(lambda x: AppInstance.FromDict(x), apps))
            else:
                return []
        except Exception as e: 
            Sentry.ExceptionNoSend("Failed to load apps", e)
            return []

    # !! Platform Command Handler Interface Function !!
    #
    # This must receive a lsit of AppInstnace
    #
    def SetAllApps(self, apps:[AppInstance]):
        dict_apps = list(map(lambda x: dict(
            x.ToDict()
        ), apps))

        with open(self.data_file, 'w') as outfile:
            json.dump(dict_apps, outfile)

        self.SendSettingsPluginMessage(dict_apps)


    def UpgradeDataStructure(self):
        try:
            if not os.path.isfile(self.data_file):
                Sentry.Info("APPS", "Updating data structure to: %s" %self.data_file)
                apps = self.parent._settings.get(["registeredApps"])
                self.set_apps(apps)
                Sentry.Debug("APPS", "Saved data to: %s" % self.data_file)
                self.parent._settings.remove(["registeredApps"])
        except Exception as e:
             Sentry.ExceptionNoSend("Failed to load apps", e)

    def UpgradeExpirationDate(self):
        try:
            def add_expiration(app):
                app["expireAt"] = app.get("expireAt", None) or self.get_default_expiration_from_now()
                return app

            apps = self.get_apps()
            apps = list(map(lambda app: add_expiration(app), apps))
            self.set_apps(apps)
        except Exception as e:
             Sentry.ExceptionNoSend("Failed to upgrade expiration", e)

    def SendSettingsPluginMessage(self, apps):
        mapped_apps = list(map(lambda x: dict(
            displayName=x.get("displayName", None),
            lastSeenAt=x.get("lastSeenAt", None),
            expireAt=x.get("expireAt", None),
            displayDescription=x.get("displayDescription", None)
        ), apps))
        mapped_apps = sorted(mapped_apps, key=lambda d: d.get("expireAt", None) or float('inf'))
        self.parent._plugin_manager.send_plugin_message("%s.settings" % self.parent._identifier, {"apps": mapped_apps})


    