
import os
import json
import time
import flask
from .subplugin import OctoAppSubPlugin
from octoprint.access.permissions import Permissions
from octoprint.events import Events
from octoapp.sentry import Sentry
from octoapp.appsstorage import AppInstance, AppStorageHelper

class OctoPrintAppStorageSubPlugin(OctoAppSubPlugin):

    def __init__(self, parent):
        super().__init__(parent)
        self.DataFile = None


    def OnAfterStartup(self):
        self.DataFile = os.path.join(self.parent.get_plugin_data_folder(), "apps.json")
        Sentry.Info("NOTIFICATION", "Using config file %s" % self.DataFile)
        self.UpgradeDataStructure()
        self.UpgradeExpirationDate()
        self.SendSettingsPluginMessage(self.GetAllApps())


    # !! Platform Command Handler Interface Function !!
    #
    # This must return a list of AppInstance
    #
    def GetAllApps(self) -> [AppInstance]:
        try: 
            if os.path.isfile(self.DataFile):
                with open(self.DataFile, 'r') as file:
                    apps = json.load(file)
                if apps is None:
                    apps = []
                
                return list(map(lambda x: AppInstance.FromDict(x), apps))
            else:
                return []
        except Exception as e: 
            Sentry.ExceptionNoSend("Failed to load apps", e)
            raise e
        

    def OnEvent(self, event, payload):
        if event == Events.CLIENT_OPENED and self.DataFile is not None:
            self.SendSettingsPluginMessage(self.GetAllApps())


    # !! Platform Command Handler Interface Function !!
    #
    # This must receive a lsit of AppInstnace
    #
    def RemoveApps(self, apps:[AppInstance]):
        filtered_apps = list(filter(lambda app: any(app.FcmToken != x.FcmToken for x in apps), self.GetAllApps()))
        self.SetAllApps(filtered_apps)
        
    def SetAllApps(self, apps:[AppInstance]):
        mapped_apps = list(map(lambda x: x.ToDict(), apps))

        with open(self.DataFile, 'w') as outfile:
            json.dump(mapped_apps, outfile)

        self.SendSettingsPluginMessage(apps)


    def UpgradeDataStructure(self):
        try:
            if not os.path.isfile(self.DataFile):
                Sentry.Info("APPS", "Dropping old app storage")
                self.parent._settings.remove(["registeredApps"])
        except Exception as e:
             Sentry.ExceptionNoSend("Failed to drop old app storage", e)


    def UpgradeExpirationDate(self):
        try:
            def add_expiration(app):
                before = app.ExpireAt
                app.ExpireAt = app.ExpireAt or AppStorageHelper.Get().GetDefaultExpirationFromNow()
                Sentry.Info("APPS", "Updating expire at for %s: %s => %s" % (app.InstanceId, before, app.ExpireAt))
                return app

            apps = self.GetAllApps()
            Sentry.Info("APPS", "Ensuring all apps have expiration dates")
            apps = list(map(lambda app: add_expiration(app), apps))
            self.SetAllApps(apps)
        except Exception as e:
             Sentry.ExceptionNoSend("Failed to upgrade expiration", e)


    def SendSettingsPluginMessage(self, apps):
        mapped_apps = list(map(lambda x: dict(
            displayName=x.DisplayName,
            lastSeenAt=x.LastSeenAt,
            expireAt=x.ExpireAt,
            displayDescription=x.DisplayDescription,
            appVersion=x.AppVersion,
            appOutdated=x.AppBuild < 1_18_000
        ), apps))
        mapped_apps = sorted(mapped_apps, key=lambda d: d.get("expireAt", None) or float('inf'))
        self.parent._plugin_manager.send_plugin_message("%s.settings" % self.parent._identifier, {"apps": mapped_apps})


    def OnApiCommand(self, command, data):
        if command == "registerForNotifications":
            if not Permissions.PLUGIN_OCTOAPP_RECEIVE_NOTIFICATIONS.can():
                return flask.make_response("Insufficient rights", 403)

            fcmToken = data["fcmToken"]

            # if this is a temporary app, remove all other temp apps for this instance
            instnace_id = data.get("instanceId", None)
            if fcmToken.startswith("activity:") and instnace_id is not None:
                AppStorageHelper.Get().RemoveTemporaryApps()

            # load apps and filter the given FCM token out
            apps = self.GetAllApps()
            if apps:
                apps = [app for app in apps if app.FcmToken != fcmToken]
            else:
                apps = []

            # add app for new registration
            apps.append(
                AppInstance(
                    fcmToken=fcmToken,
                    fcmFallbackToken=data.get("fcmTokenFallback", None),
                    instanceId=data["instanceId"],
                    displayName=data["displayName"],
                    displayDescription=data.get("displayDescription", None),
                    model=data["model"],
                    appVersion=data["appVersion"],
                    appBuild=data["appBuild"],
                    appLanguage=data["appLanguage"],
                    lastSeenAt=time.time(),
                    expireAt=(time.time() + data["expireInSecs"]) if "expireInSecs" in data else AppStorageHelper.Get().GetDefaultExpirationFromNow(),
                )
            )

            # save
            Sentry.Info("NOTIFICATION", "Registered app %s" % fcmToken)
            self.SetAllApps(apps)
            self.parent._settings.save()
            return flask.jsonify(dict())
        
        else: 
            return None