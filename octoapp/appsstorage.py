from abc import abstractmethod
from octoapp.sentry import Sentry
from typing import List, Dict, Optional, Any
import time

class AppInstance:
        
    def __init__(
              self, 
              fcmToken:str, 
              fcmFallbackToken:Optional[str], 
              activityAutoStartToken:Optional[str],
              instanceId:str,
              displayName:str,
              displayDescription:Optional[str],
              model:str,
              appVersion:str,
              appBuild:int,
              appLanguage:str,
              lastSeenAt:float,
              expireAt:float,
              excludeNotifications:List[str],
    ):
        self.FcmToken = fcmToken
        self.FcmFallbackToken:Optional[str] = fcmFallbackToken
        self.InstanceId = instanceId
        self.ActivityAutoStartToken = activityAutoStartToken
        self.DisplayName = displayName
        self.DisplayDescription = displayDescription
        self.Model = model
        self.AppVersion = appVersion
        self.AppBuild = appBuild
        self.AppLanguage = appLanguage
        self.LastSeenAt = lastSeenAt
        self.ExpireAt = expireAt
        self.ExcludeNotifications = excludeNotifications

    def WithToken(self, fcmToken: str):
        return AppInstance(
            fcmToken=fcmToken,
            fcmFallbackToken=self.FcmFallbackToken,
            activityAutoStartToken=self.ActivityAutoStartToken,
            instanceId=self.InstanceId,
            displayName=self.DisplayName,
            displayDescription=self.DisplayDescription,
            model=self.Model,
            appVersion=self.AppVersion,
            appBuild=self.AppBuild,
            appLanguage=self.AppLanguage,
            lastSeenAt=self.LastSeenAt,
            expireAt=self.ExpireAt,
            excludeNotifications=self.ExcludeNotifications,
        )

    def ToDict(self): 
        return dict(
            fcmToken=self.FcmToken,
            fcmTokenFallback=self.FcmFallbackToken,
            instanceId=self.InstanceId,
            displayName=self.DisplayName,
            displayDescription=self.DisplayDescription,
            activityAutoStartToken=self.ActivityAutoStartToken,
            model=self.Model,
            appVersion=self.AppVersion,
            appBuild=self.AppBuild,
            appLanguage=self.AppLanguage,
            lastSeenAt=self.LastSeenAt,
            expireAt=self.ExpireAt,
            excludeNotifications=self.ExcludeNotifications
        )

    @staticmethod
    def FromDict(dict:Dict[str,Any]):
        def ensure_string_list(value: Any) -> List[str]:
            if not isinstance(value, list):
                return []
            return [str(item) for item in value]

        return AppInstance(
            fcmToken=dict["fcmToken"],
            fcmFallbackToken=dict.get("fcmTokenFallback", None),
            instanceId=dict["instanceId"],
            displayName=dict.get("displayName", "Unknown"),
            displayDescription=dict.get("displayDescription", ""),
            model=dict.get("model", "Unknown"),
            activityAutoStartToken=dict.get("activityAutoStartToken", None),
            appVersion=dict.get("appVersion", "Unknown"),
            appBuild=int(dict.get("appBuild", 1)),
            appLanguage=dict.get("appLanguage", "en"),
            lastSeenAt=int(dict.get("lastSeenAt", 0)),
            expireAt=int(dict.get("expireAt", 0)),
            excludeNotifications=ensure_string_list(dict.get("excludeNotifications", []))
        )
       

class AppStoragePlatformHelper:
    @abstractmethod
    def GetAllApps(self) -> List[AppInstance]:
        return []
    
    @abstractmethod
    def RemoveApps(self, apps:List[AppInstance]):
        pass

    @abstractmethod
    def GetOrCreateEncryptionKey(self) -> str:
        pass
  
class AppStorageHelper:

    # Logic for a static singleton
    _Instance:Optional["AppStorageHelper"] = None

    @staticmethod
    def Init(appStoragePlatformHelper:AppStoragePlatformHelper):
        AppStorageHelper._Instance = AppStorageHelper(appStoragePlatformHelper)

    @staticmethod
    def Get() -> "AppStorageHelper":
        if AppStorageHelper._Instance is not None:
            return AppStorageHelper._Instance 
        else:
            raise Exception("AppStorageHelper not intialized")

    def __init__(self, appStoragePlatformHelper:AppStoragePlatformHelper):
        self.AppStoragePlatformHelper = appStoragePlatformHelper

    def GetAndroidApps(self, apps:List[AppInstance]) -> List[AppInstance]:
        return list(filter(lambda app: not app.FcmToken.startswith("activity:") and not app.FcmToken.startswith("ios:"), apps))

    def GetExpiredApps(self, apps:List[AppInstance]) -> List[AppInstance]:
        return list(filter(lambda app: app.ExpireAt is not None and time.time() > app.ExpireAt, apps))

    def GetIosApps(self, apps:List[AppInstance]) -> List[AppInstance]:
        return list(filter(lambda app: app.FcmToken.startswith("ios:"), apps))

    def GetActivities(self, apps:List[AppInstance]) -> List[AppInstance]:
        return list(sorted(filter(lambda app: app.FcmToken.startswith("activity:"), apps), key=lambda app: app.LastSeenAt, reverse=True))
    
    def GetActivityAutoStarts(self, apps:List[AppInstance]) -> List[AppInstance]:
        return list(sorted(filter(lambda app: app.ActivityAutoStartToken is not None, apps), key=lambda app: app.LastSeenAt, reverse=True))
    
    def GetDefaultExpirationFromNow(self):
        return (time.time() + 2592000)

    def LogApps(self):
        apps = self.GetAllApps()
        Sentry.Debug("APPS", "Now %s apps registered" % len(apps))
        for app in apps:
            Sentry.Debug("APPS", "     => %s" % app.FcmToken[0:100])

    def RemoveTemporaryApps(self, for_instance_id:Optional[str]=None):
        apps = self.GetAllApps()
        
        if for_instance_id is None:
            apps = list(filter(lambda app: app.FcmToken.startswith("activity:"), apps))
            Sentry.Debug("APPS", "Removed all temporary apps")
        else:
            apps = list(filter(lambda app: app.FcmToken.startswith("activity:") and app.InstanceId == for_instance_id , apps))
            Sentry.Debug("APPS", "Removed all temporary apps for %s" % for_instance_id)

        self.RemoveApps(apps)

    def GetAllApps(self) -> List[AppInstance]:
        apps = self.AppStoragePlatformHelper.GetAllApps()
        Sentry.Debug("APPS", "Loading %s apps" % len(apps))
        return apps

    def RemoveApps(self, apps: List[AppInstance]):
        Sentry.Debug("APPS", "Removing %s apps" % len(apps))
        self.AppStoragePlatformHelper.RemoveApps(apps)
        self.LogApps()
     
    def GetOrCreateEncryptionKey(self):
        return self.AppStoragePlatformHelper.GetOrCreateEncryptionKey()
