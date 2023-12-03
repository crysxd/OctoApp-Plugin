
import threading
import requests
import json
import time
import base64
import hashlib
from Crypto.Cipher import AES
from Crypto import Random
from .sentry import Sentry
from .appsstorage import AppStorageHelper

class NotificationSender:

    EVENT_PAUSED="paused"
    EVENT_FILAMENT_REQUIRED="filamentchange"
    EVENT_USER_INTERACTION_NEEDED="userinteractionneeded"
    EVENT_TIME_PROGRESS="timerprogress"
    EVENT_DONE="done"
    EVENT_CANCELLED="cancelled"
    EVENT_PROGRESS="progress"
    EVENT_STARTED="started"
    EVENT_ERROR="error"
    EVENT_MMU2_FILAMENT_START="mmu_filament_selection_started"
    EVENT_MMU2_FILAMENT_DONE="mmu_filament_selection_completed"
    EVENT_BEEP="beep"
    EVENT_RESUME="resume"
    EVENT_THIRD_LAYER_DONE="third_layer_done"
    EVENT_FIRST_LAYER_DONE="first_layer_done"


    def __init__(self):
        self.LastPrintState = {}
        self.LastProgressUpdate = 0
        self.PrinterName = "Printer"
        self.DefaultConfig = dict(
            updatePercentModulus=5,
            highPrecisionRangeStart=5,
            highPrecisionRangeEnd=5,
            minIntervalSecs=300,
            sendNotificationUrl="https://europe-west1-octoapp-4e438.cloudfunctions.net/sendNotificationV2",
        )
        self.CachedConfig = self.DefaultConfig
        self.CachedConfigAt = 0
        self._continuouslyCheckActivitiesExpired()
        self._continuouslyUpdateConfig()

    def SendNotification(self, event, state=None):
        try:
            if state is None:
                state = self.LastPrintState

            if event == self.EVENT_DONE:
                state["ProgressPercentage"] = 100

            self.LastPrintState = state
            helper = AppStorageHelper.Get()
            Sentry.Info("SENDER", "Preparing notification for %s" % event)
            onlyActivities = self._shouldSendOnlyActivities(event=event, state=state)
            targets = self._getPushTargets(
                preferActivity=self._shouldPreferActivity(event),
                canUseNonActivity=self._canUseNonActivity(event) and not onlyActivities
            )

            if onlyActivities:
                Sentry.Debug("SENDER", "Only activities allowed, filtering")
                targets = helper.GetActivities(targets)

            if not targets:
                Sentry.Debug("SENDER", "No targets, skipping notification")
                return

            ios_targets = helper.GetIosApps(targets)
            activity_targets = helper.GetActivities(targets)
            android_targets = helper.GetAndroidApps(targets)
            apnsData = self._createApnsPushData(event, state) if len(ios_targets) or len(activity_targets) else None

            if not len(android_targets) and apnsData is None:
                Sentry.Info("SENDER", "Skipping push, no Android targets and no APNS data, skipping notification")
                return
            
            if not len(android_targets) and not len(activity_targets) and apnsData.get("alert", None) is None:
                Sentry.Info("SENDER", "Skipping push, no Android targets, no iOS targets and APNS data has no alert, skipping notification")
                return

            self._doSendNotification(
                targets=targets,
                highProiroty=not onlyActivities,
                apnsData=apnsData,
                androidData=self._createAndroidPushData(event, state)
            )

            # Remove temporary apps after getting targets
            if event == self.EVENT_CANCELLED or event == self.EVENT_DONE:
                helper.RemoveTemporaryApps()
        except Exception as e:
            Sentry.ExceptionNoSend("Failed to send notification", e)
    
    def _shouldSendOnlyActivities(self, event, state):
        if event == self.EVENT_STARTED:
            self.LastProgressUpdate = time.time()
            return False

        # If the event is not progress, send to all (including time progress)
        elif event != self.EVENT_PROGRESS:
            return False
        
        # Sanity check
        elif self.CachedConfig is None:
            Sentry.Warn("SENDER", "No config cached!")
            return True
        
        modulus = self.CachedConfig["updatePercentModulus"]
        highPrecisionStart = self.CachedConfig["highPrecisionRangeStart"]
        highPrecisionEnd = self.CachedConfig["highPrecisionRangeEnd"]
        minIntervalSecs = self.CachedConfig["minIntervalSecs"]
        time_since_last = time.time() - self.LastProgressUpdate
        progress = int(state["ProgressPercentage"])
        if progress < 100 and progress > 0 and (
            (progress % modulus) == 0
            or progress <= highPrecisionStart
            or progress >= (100 - highPrecisionEnd)
        ):
            Sentry.Debug("SENDER", "Updating progress in main interval: %s" % progress)
            self.LastProgressUpdate = time.time()
            return False
        elif time_since_last > minIntervalSecs:
            Sentry.Debug("SENDER", "Over %s sec passed since last progress update, sending low priority update" % int(time_since_last))
            self.LastProgressUpdate = time.time()
            return False
        else:
            Sentry.Debug("SENDER", "Skipping progress update, only %s seconds passed since last" % int(time_since_last))
            return True
    

    def _doSendNotification(self, targets, highProiroty, apnsData, androidData):
        try:
            if not len(targets): 
                Sentry.Info("SENDER", "No targets, skipping send")
                return

            # Base priority on onlyActivities. If the flag is set this is a low
            # priority status update
            body = dict(
                targets=list(map(lambda x: {
                    "fcmToken": x.FcmToken,
                    "fcmTokenFallback": x.FcmFallbackToken,
                    "instanceId": x.InstanceId
                }, targets)),
                highPriority=highProiroty,
                androidData=androidData,
                apnsData=apnsData,
            )

            Sentry.Info("SENDER", "Sending notification: %s" % json.dumps(body))

            # Make request and check 200
            r = requests.post(
                self.CachedConfig["sendNotificationUrl"],
                timeout=float(10), 
                json=body
            )
            if r.status_code != requests.codes.ok:
                raise Exception("Unexpected response code %d: %s" % (r.status_code, r.text))
            else:
                Sentry.Info("SENDER", "Send to %s was success %s" % (len(targets), r.json()))

            # Delete invalid tokens
            apps = AppStorageHelper.Get().GetAllApps()
            invalid_tokens = r.json()["invalidTokens"]
            for fcmToken in invalid_tokens:
                Sentry.Info("SENDER", "Removing %s, no longer valid" % fcmToken)
                apps = [app for app in apps if app.FcmToken == fcmToken or app.FcmFallbackToken == fcmToken]
                AppStorageHelper.Get().RemoveApps(apps)

        except Exception as e:
            Sentry.ExceptionNoSend("Failed to send notification %s", e)

    def _createAndroidPushData(self, event, state):
        data = {}
        if event == self.EVENT_BEEP:
            data = { "type": "beep" }
        else:
            type = None
            if event == self.EVENT_PROGRESS or event == self.EVENT_STARTED or event == self.EVENT_TIME_PROGRESS or event == self.EVENT_RESUME:
                type = "printing"
            elif event == self.EVENT_FIRST_LAYER_DONE:
                type = "first_layer_done"
            elif event == self.EVENT_FIRST_LAYER_DONE:
                type = "third_layer_done"
            elif event == self.EVENT_PAUSED:
                type = "paused"
            elif event == self.EVENT_DONE:
                type = "completed"
            elif event == self.EVENT_ERROR:
                type = "error"
            elif event == self.EVENT_FILAMENT_REQUIRED:
                type = "filament_required"
            elif event == self.EVENT_USER_INTERACTION_NEEDED:
                type = "paused_gcode"
            elif event == self.EVENT_MMU2_FILAMENT_START:
                type = "mmu_filament_selection_started"
            elif event == self.EVENT_MMU2_FILAMENT_DONE:
                type = "mmu_filament_selection_completed"
            elif event == self.EVENT_CANCELLED:
                type = "idle"

            data = {
                "serverTime": int(time.time()),
                "serverTimePrecise": time.time(),
                "printId": state.get("PrintId", None),
                "fileName": state.get("FileName", None),
                "progress": state.get("ProgressPercentage", None),
                "timeLeft": state.get("TimeRemainingSec", None),
                "type": type
            }

        try:
            cipher = AESCipher(AppStorageHelper.Get().GetOrCreateEncryptionKey())
            return cipher.encrypt(json.dumps(data))
        except Exception as e:
            Sentry.ExceptionNoSend(e)
            return json.dumps(data)
        
    
    def _createApnsPushData(self, event, state):
        Sentry.Info("SENDER", "Targets contain iOS devices, generating texts for '%s'" % event)
        notificationTitle = None
        notificationBody = None
        notificationSound = None
        liveActivityState = None

        if event == self.EVENT_BEEP:
            return {
                "alert": {
                    "title": "Beep!",
                    "body": "%s needs attention" % self.PrinterName,
                },
                "sound": "default",
            }
        
        elif event == self.EVENT_STARTED:
            return {
                "alert": {
                    "title": "%s started to print" % self.PrinterName,
                    "body": "Open the app to see the progress",
                },
                "sound": "default",
            }
    
        elif event == self.EVENT_PROGRESS or event == self.EVENT_TIME_PROGRESS or event == self.EVENT_RESUME:
            liveActivityState = "printing"

        elif event == self.EVENT_FIRST_LAYER_DONE:
            notificationTitle = "First layer completed"
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "printing"

        elif event == self.EVENT_THIRD_LAYER_DONE:
            notificationTitle = "Third layer is completed"
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "printing"

        elif event == self.EVENT_CANCELLED:
            liveActivityState = "cancelled"

        elif event == self.EVENT_DONE:
            notificationTitle = "%s is done!" % self.PrinterName
            notificationBody = state.get("FileName", None)
            notificationSound = "notification_print_done.wav"
            liveActivityState = "completed"

        elif event == self.EVENT_FILAMENT_REQUIRED:
            notificationTitle = "Filament required"
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "filamentRequired"

        elif event == self.EVENT_USER_INTERACTION_NEEDED:
            notificationTitle = "Print paused"
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "pausedGcode"

        elif event == self.EVENT_PAUSED:
            liveActivityState = "paused"
        
        elif event == self.EVENT_MMU2_FILAMENT_START:
            notificationTitle = "MMU2 filament selection required"
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "filamentRequired"

        elif event == self.EVENT_MMU2_FILAMENT_DONE:
            liveActivityState = "printing"

        elif event == self.EVENT_ERROR:
            liveActivityState = "error"

        else:
            Sentry.Warn("SENDER", "Missing handling for '%s'" % event)
            return None

        # Let's only end the activity on cancel. If we end it on completed the alert isn't shown
        data = self._createActivityContentState(
            isEnd=event == self.EVENT_CANCELLED,
            state=state,
            liveActivityState=liveActivityState
        )

        # Delay cancel or complete notification to ensure it's last
        if event == self.EVENT_CANCELLED or event == self.EVENT_DONE:
            time.sleep(5)

        if notificationSound is not None:
            data["sound"] = notificationSound
        
        if notificationBody is None:
            notificationBody = "Time to check %s!" % self.PrinterName

        if notificationTitle is not None:
            data["alert"] = {
                "title": notificationTitle,
                "body": notificationBody
            }

        return data

    def _createActivityContentState(self, isEnd, state, liveActivityState):
        return {
            "event": "end" if isEnd else "update",
            "content-state": {
                "fileName": state.get("FileName", None),
                "progress": int(float(state.get("ProgressPercentage", None))),
                "sourceTime": int(time.time() * 1000),
                "state": liveActivityState,
                "timeLeft": int(float(state.get("TimeRemainingSec", None))),
                "printTime": int(float(state.get("DurationSec", None))),
            }
        }

    def _shouldPreferActivity(self, event):
        return event != self.EVENT_BEEP and event != self.EVENT_FIRST_LAYER_DONE and event != self.EVENT_THIRD_LAYER_DONE

    def _canUseNonActivity(self, event):
        return event != self.EVENT_PROGRESS and event != self.EVENT_PROGRESS and event != self.EVENT_RESUME and event != self.EVENT_TIME_PROGRESS

    def _getPushTargets(self, preferActivity, canUseNonActivity):
        Sentry.Info("SENDER", "Finding targets preferActivity=%s canUseNonActivity=%s" % (preferActivity, canUseNonActivity))
        helper = AppStorageHelper.Get()
        apps = helper.GetAllApps()
        phones = {}

        # Group all apps by phone
        for app in apps:
            instance_id = app.InstanceId
            phone = phones.get(instance_id, [])
            phone.append(app)
            phones[instance_id] = phone

        # Pick activity if available, otherwise any other app
        def pick_best_app(apps):
            activities = helper.GetActivities(apps)
            ios = helper.GetIosApps(apps)
            android = helper.GetAndroidApps(apps)

            # If we have an activity and we should prefer it, use it
            if len(activities) and preferActivity:
                return activities[0:1]
            
            # If we have an iOS app and we can use non-activity targets, use it
            # This means iOS might not be picked at all if we only can use activity but no activity is available!
            elif len(ios) and canUseNonActivity:
                return ios[0:1]

            # If we have any android devices, use all of them (might be watch + phone)
            elif len(android):
                return android
            
            # Oh no!
            else:
                return []

        # Get apps per phone and flatten
        apps = list(map(lambda phone: pick_best_app(phone), phones.values()))
        apps = [app for sublist in apps for app in sublist]
        return list(filter(lambda app: app is not None, apps))


    def _continuouslyCheckActivitiesExpired(self):
        t = threading.Thread(
            target=self._doContinuouslyCheckActivitiesExpired,
            args=[]
        )
        t.daemon = True
        t.start()

    def _doContinuouslyCheckActivitiesExpired(self):
         Sentry.Debug("SENDER", "Checking for expired apps every 60s")
         while True:
            time.sleep(60)

            try:
                helper = AppStorageHelper.Get()
                expired = helper.GetExpiredApps(helper.GetAllApps())
                if len(expired):
                    Sentry.Debug("SENDER", "Found %s expired apps" % len(expired))
                    helper.LogApps()

                    expired_activities = helper.GetActivities(expired)
                    if len(expired_activities):
                        # This will end the live activity, we currently do not send a notification to inform
                        # the user, we can do so by setting isEnd=False and the apnsData as below
                        apnsData=self._createActivityContentState(
                            isEnd=True,
                            liveActivityState="expired",
                            state=self.LastPrintState
                        )
                        # apnsData["alert"] = {
                        #     "title": "Updates paused for %s" % self.LastPrintState.get("name", ""),
                        #     "body": "Live activities expire after 8h, open OctoApp to renew"
                        # }
                        self._doSendNotification(
                            targets=expired_activities,
                            highProiroty=True,
                            apnsData=apnsData,
                            androidData="none"
                        )

                    helper.RemoveApps(expired)
                    Sentry.Debug("SENDER", "Cleaned up expired apps")


            except Exception as e:
                Sentry.ExceptionNoSend("Failed to retire expired", e)


    #
    # CONFIG
    #

    def _continuouslyUpdateConfig(self):
        Sentry.Info("SENDER", "Updating config")
        t = threading.Thread(target=self._doContinuouslyUpdateConfig)
        t.daemon = True
        t.start()

    def _doContinuouslyUpdateConfig(self):
        while True:
            time.sleep(3600)
            # If we have no config cached or the cache is older than a day, request new config
            cache_config_max_age = time.time() - 86400
            if self.CachedConfigAt > cache_config_max_age:
                Sentry.Info("SENDER", "Config still valid")

            # Request config, fall back to default
            try:
                r = requests.get(
                    "https://www.octoapp.eu/config/plugin.json", timeout=float(15)
                )
                if r.status_code != requests.codes.ok:
                    raise Exception("Unexpected response code %d" % r.status_code)
                self.CachedConfig = r.json()
                self.CachedConfigAt = time.time()
        
                Sentry.Info("SENDER", "OctoApp loaded config: %s" % self.CachedConfig)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to fetch config using defaults for 5 minutes", e)
                self.CachedConfig = self.DefaultConfig
                self.CachedConfigAt = cache_config_max_age + 300

class AESCipher(object):
    def __init__(self, key):
        self.bs = AES.block_size
        self.key = hashlib.sha256(key.encode()).digest()

    def encrypt(self, raw):
        raw = self._pad(raw)
        iv = Random.new().read(AES.block_size)
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return base64.b64encode(iv + cipher.encrypt(raw.encode())).decode("utf-8")

    def _pad(self, s):
        return s + (self.bs - len(s) % self.bs) * chr(self.bs - len(s) % self.bs)
            
