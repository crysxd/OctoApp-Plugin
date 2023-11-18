
from octoapp.sentry import Sentry
import threading
import time

class AppInstance:
        
    def __init__(
              self, 
              fcmToken:str, 
              fcmFallbackToken:str, 
              instanceId:str,
              displayName:str,
              displayDescription:str,
              model:str,
              appVersion:str,
              appBuild:int,
              appLanguage:str,
              lastSeenAt:float,
              expireAt:float,
    ):
        self.FcmToken = fcmToken
        self.FcmFallbackToken = fcmFallbackToken
        self.InstanceId = instanceId
        self.DisplayName = displayName
        self.DisplayDescription = displayDescription
        self.Model = model
        self.AppVersion = appVersion
        self.AppBuild = appBuild
        self.AppLanguage = appLanguage
        self.LastSeenAt = lastSeenAt
        self.ExpireAt = expireAt


    def ToDict(self): 
        return dict(
            fcmToken=self.FcmToken,
            fcmTokenFallback=self.FcmFallbackToken,
            instanceId=self.InstanceId,
            displayName=self.DisplayName,
            displayDescription=self.DisplayDescription,
            model=self.Model,
            appVersion=self.AppVersion,
            appBuild=self.AppBuild,
            appLanguage=self.AppLanguage,
            lastSeenAt=self.LastSeenAt,
            expireAt=self.ExpireAt
        )

    @staticmethod
    def FromDict(dict:dict):
        return AppInstance(
            fcmToken=dict["fcmToken"],
            fcmFallbackToken=dict["fcmTokenFallback"],
            instanceId=dict["instanceId"],
            displayName=dict["displayName"],
            displayDescription=dict["displayDescription"],
            model=dict["model"],
            appVersion=dict["appVersion"],
            appBuild=dict["appBuild"],
            appLanguage=dict["appLanguage"],
            lastSeenAt=dict["lastSeenAt"],
            expireAt=dict["expireAt"],
        )

       

class AppStorageHelper:

    # Logic for a static singleton
    _Instance = None

    @staticmethod
    def Init(appStoragePlatformHelper):
        AppStorageHelper._Instance = AppStorageHelper(appStoragePlatformHelper)

    @staticmethod
    def Get():
        return AppStorageHelper._Instance

    def __init__(self, appStoragePlatformHelper):
        self.AppStoragePlatformHelper = appStoragePlatformHelper


    def continuously_check_activities_expired(self):
        t = threading.Thread(
            target=self.do_continuously_check_activities_expired,
            args=[]
        )
        t.daemon = True
        t.start()

    def do_continuously_check_activities_expired(self):
         Sentry.Debug("APPS", "Checking for expired apps every 60s")
         while True:
            time.sleep(60)

            try:
                expired = self.get_expired_apps(self.get_activities(self.get_apps()))
                if len(expired):
                    Sentry.Debug("APPS", "Found %s expired apps" % len(expired))
                    self.LogApps()

                    expired_activities = self.GetActivities(expired)
                    if len(expired_activities):
                        # This will end the live activity, we currently do not send a notification to inform
                        # the user, we can do so by setting is_end=False and the apnsData as below
                        apnsData=self.create_activity_content_state(
                            is_end=True,
                            liveActivityState="expired",
                            state=self.print_state
                        )
                        # apnsData["alert"] = {
                        #     "title": "Updates paused for %s" % self.print_state.get("name", ""),
                        #     "body": "Live activities expire after 8h, open OctoApp to renew"
                        # }
                        self.send_notification_blocking_raw(
                            targets=expired_activities,
                            high_priority=True,
                            apnsData=apnsData,
                            androidData="none"
                        )

                    filtered_apps = list(filter(lambda app: any(app.fcmToken != x.fcmToken for x in expired), self.get_apps()))
                    self.SetApps(filtered_apps)
                    self.LogApps()
                    Sentry.Debug("APPS", "Cleaned up expired apps")


            except Exception as e:
                Sentry.ExceptionNoSend("Failed to retire expired", e)


    def GetAndroidApps(self, apps):
        return list(filter(lambda app: not app.fcmToken.startswith("activity:") and not app.fcmToken.startswith("ios:"), apps))

    def GetExpiredApps(self, apps):
        return list(filter(lambda app: app.expireAt is not None and time.time() > app.expireAt, apps))

    def GetIosApps(self, apps):
        return list(filter(lambda app: app.fcmToken.startswith("ios:"), apps))

    def GetActivities(self, apps):
        return list(filter(lambda app: app.fcmToken.startswith("activity:"), apps))
    
    def GetDefaultExpirationFromNow(self):
        return (time.time() + 2592000)

    def LogApps(self):
        self.AppStoragePlatformHelper.LogAllApps()

    def RemoveTemporaryApps(self, for_instance_id=None):
        apps = self.GetApps()
        
        if for_instance_id is None:
            apps = list(filter(lambda app: not app.fcmToken.startswith("activity:") ,apps))
            Sentry.Debug("APPS", "Removed all temporary apps")
        else:
            apps = list(filter(lambda app: not app.fcmToken.startswith("activity:") or app.instanceId != for_instance_id ,apps))
            Sentry.Debug("APPS", "Removed all temporary apps for %s" % for_instance_id)

        self.SetApps(apps)

    def GetAllApps(self) -> [AppInstance]:
        apps = self.AppStoragePlatformHelper.GetAllApps()
        Sentry.Debug("APPS", "Loading %s apps" % len(apps))
        return apps

    def SetAllApps(self, apps:[AppInstance]):
        Sentry.Debug("APPS", "Storing %s apps" % len(apps))
        self.AppStoragePlatformHelper.SetAllApps(apps)