import json
import os
import threading
import time
import uuid

from typing import Any, Dict, List, Optional

import flask
from octoprint.access.permissions import Permissions
from octoprint.events import Events

from octoapp.logging import LoggerLike
from octoapp.sentry import Sentry
from octoapp.appsstorage import (AppInstance, AppStorageHelper,
                                 AppStoragePlatformHelper)

from .subplugin import IOctoAppSubPluginParent, OctoAppSubPlugin


# pylint: disable=protected-access
class OctoPrintAppStorageSubPlugin(OctoAppSubPlugin, AppStoragePlatformHelper):

    def __init__(self, logger: LoggerLike, parent: IOctoAppSubPluginParent):
        super().__init__(logger, parent)
        self.DataFile: Optional[str] = None
        self.Lock = threading.Lock()
        self.DataFile = os.path.join(self.parent.get_plugin_data_folder(), "apps.json")
        self.logger.info(f"Using config file {self.DataFile}")
        self.logger.debug("-> __init__")
        with self.Lock:
            logger.debug("<- __init__")
            self._upgradeDataStructure()
            self._upgradeExpirationDate()
            self._sendSettingsPluginMessage(self._getAllApps())
            self._getOrCreateEncryptionKey()


    # !! Platform Command Handler Interface Function !!
    #
    # This must return a list of AppInstance
    #
    def GetAllApps(self) -> List[AppInstance]:
        self.logger.debug("-> GetAllApps")
        with self.Lock:
            self.logger.debug("<- GetAllApps")
            return self._getAllApps()

    def OnEvent(self, event: str, payload: Dict[str, Any]):
        if event == Events.CLIENT_OPENED and self.DataFile is not None:
            self.logger.debug("-> OnEvent")
            with self.Lock:
                self.logger.debug("<- OnEvent")
                self._sendSettingsPluginMessage(self._getAllApps())


    # !! Platform Command Handler Interface Function !!
    #
    # This must receive a lsit of AppInstnace
    #
    def RemoveApps(self, apps:List[AppInstance]):
        self.logger.debug("-> RemoveApps")
        with self.Lock:
            self.logger.debug("<- RemoveApps")
            allApps = self._getAllApps()
            for appToRemove in apps:
                allApps = [app for app in allApps if app.FcmToken != appToRemove.FcmToken]

            self._setAllApps(allApps)


    def OnApiCommand(self, command:str, data:Dict[str,Any]):
        if command == "registerForNotifications":
            if not Permissions.PLUGIN_OCTOAPP_RECEIVE_NOTIFICATIONS.can(): # type: ignore
                return flask.make_response("Insufficient rights", 403)

            fcmToken = data["fcmToken"]

            self.logger.debug("-> OnApiCommand")
            with self.Lock:
                self.logger.debug("<- OnApiCommand")
                # load apps and filter the given FCM token out
                apps = self._getAllApps()
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
                        displayDescription=data.get("displayDescription", "None"),
                        activityAutoStartToken=data.get("activityAutoStartToken", None),
                        model=data["model"],
                        appVersion=data["appVersion"],
                        appBuild=data["appBuild"],
                        appLanguage=data["appLanguage"],
                        lastSeenAt=time.time(),
                        expireAt=(time.time() + data["expireInSecs"]) if "expireInSecs" in data else AppStorageHelper.Get().GetDefaultExpirationFromNow(),
                        excludeNotifications=data.get("excludeNotifications", [])
                    )
                )

                # save
                self.logger.info(f"Registered app {fcmToken}")
                self._setAllApps(apps)
                self.parent._settings.save() #type: ignore
                return flask.jsonify(dict())

        else:
            return None

    # !! Platform Command Handler Interface Function !!
    #
    # This must receive a lsit of AppInstnace
    #
    def GetOrCreateEncryptionKey(self) -> str:
        self.logger.debug("-> GetOrCreateEncryptionKey")
        with self.Lock:
            self.logger.debug("<- GetOrCreateEncryptionKey")
            return self._getOrCreateEncryptionKey()

    def _getOrCreateEncryptionKey(self) -> str:
        key: Optional[str] = self.parent._settings.get(["encryptionKey"]) #type: ignore
        if key is None:
            key = str(uuid.uuid4())
            self.logger.info("Created new encryption key")
            self.parent._settings.set(["encryptionKey"], key) #type: ignore
        return key


    def _getAllApps(self) -> List[AppInstance]:
        try:
            if self.DataFile is not None and os.path.isfile(self.DataFile):
                with open(self.DataFile, 'r',  encoding='utf-8') as file:
                    apps = json.load(file)
                if apps is None:
                    apps = []

                return list(map(AppInstance.FromDict, apps))
            else:
                return []
        except Exception as e:
            Sentry.ExceptionNoSend("Failed to load apps", e)
            raise e


    def _setAllApps(self, apps:List[AppInstance]):
        mapped_apps = list(map(lambda x: x.ToDict(), apps))

        if self.DataFile is not None:
            with open(self.DataFile, 'w',  encoding='utf-8') as outfile:
                json.dump(mapped_apps, outfile)
        else:
            self.logger.error("Tried to set all apps but DataFile is None")

        self._sendSettingsPluginMessage(apps)

    def _upgradeDataStructure(self):
        try:
            if self.DataFile is not None and not os.path.isfile(self.DataFile):
                self.logger.info("Dropping old app storage")
                self.parent._settings.remove(["registeredApps"]) #type: ignore
        except Exception as e:
            Sentry.ExceptionNoSend("Failed to drop old app storage", e)


    def _upgradeExpirationDate(self):
        try:
            def add_expiration(app:AppInstance):
                before = app.ExpireAt
                app.ExpireAt = app.ExpireAt or AppStorageHelper.Get().GetDefaultExpirationFromNow()
                self.logger.info(f"Updating expire at for {app.InstanceId}: {before} => {app.ExpireAt}")
                return app

            apps = self._getAllApps()
            self.logger.info("Ensuring all apps have expiration dates")
            apps = list(map(add_expiration, apps))
            self._setAllApps(apps)
        except Exception as e:
            Sentry.ExceptionNoSend("Failed to upgrade expiration", e)

    def _sendSettingsPluginMessage(self, apps:List[AppInstance]):
        mapped_apps = list(map(lambda x: dict(
            displayName=x.DisplayName,
            lastSeenAt=x.LastSeenAt,
            expireAt=x.ExpireAt,
            displayDescription=x.DisplayDescription,
            appVersion=x.AppVersion,
            appOutdated=x.AppBuild < 1_17_134
        ), apps))
        mapped_apps = sorted(mapped_apps, key=lambda d: d.get("expireAt", None) or float('inf'))
        self.parent._plugin_manager.send_plugin_message(f"{self.parent._identifier}.settings", {"apps": mapped_apps}) #type:ignore
