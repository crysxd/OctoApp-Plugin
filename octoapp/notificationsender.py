
import base64
import hashlib
import json
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, cast

import requests

from .appsstorage import AppInstance, AppStorageHelper
from .logging import LoggerLike
from .sentry import Sentry


class NotificationSender:

    EVENT_PAUSED="paused"
    EVENT_FILAMENT_REQUIRED="filamentchange"
    EVENT_USER_INTERACTION_NEEDED="userinteractionneeded"
    EVENT_TIME_PROGRESS="timerprogress"
    EVENT_DONE="done"
    EVENT_CANCELLED="cancelled"
    EVENT_CUSTOM="custom"
    EVENT_PROGRESS="progress"
    EVENT_STARTED="started"
    EVENT_ERROR="error"
    EVENT_MMU2_FILAMENT_START="mmu_filament_selection_started"
    EVENT_MMU2_FILAMENT_DONE="mmu_filament_selection_completed"
    EVENT_BEEP="beep"
    EVENT_RESUME="resume"
    EVENT_THIRD_LAYER_DONE="third_layer_done"
    EVENT_FIRST_LAYER_DONE="first_layer_done"

    STATE_CUSTOM_EVENT_MESSAGE = "message"
    STATE_CUSTOM_EVENT_DETAIL = "detail"
    STATE_TIME_REMAINING_SEC = "time_remaining_sec"
    STATE_PROGRESS_PERCENT = "progress_percent"
    STATE_DURATION_SEC = "duration_sec"
    STATE_FILE_NAME = "file_name"
    STATE_ERROR = "error"
    STATE_FILE_PATH = "file_path"
    STATE_PRINT_ID = "print_id"


    def __init__(self, logger: LoggerLike):
        self.LastPrintState:Dict[str,Any] = {}
        self.LastProgressUpdate = 0
        self.LastProgressPercent = 0
        self.PrinterName = "$printer_label$" # This will be replaced by the printer label in the app
        self.DefaultConfig = dict(
            updatePercentModulus=5,
            highPrecisionRangeStart=5,
            highPrecisionRangeEnd=5,
            minIntervalSecs=300,
            sendNotificationUrl="https://europe-west1-octoapp-4e438.cloudfunctions.net/sendNotificationV2",
        )
        self.Logger = logger
        self.CachedConfig = self.DefaultConfig
        self.CachedConfigAt = 0
        self._continuouslyCheckActivitiesExpired()
        self._continuouslyUpdateConfig()

    def SendNotification(self, event:str, state:Optional[Dict[str,Any]]=None):
        helper: Optional[AppStorageHelper] = None
        try:
            helper = AppStorageHelper.Get()

            if state is None:
                state = self.LastPrintState

            if event == self.EVENT_DONE:
                state[NotificationSender.STATE_PROGRESS_PERCENT] = 100

            self.LastPrintState = state
            self.Logger.info(f"Preparing notification for {event}")
            priority = self._determinePriority(event=event, state=state)

            # Skip this event
            if  priority == -1:
                return

            targets = self._getPushTargets(event = event)

            onlyActivities = priority == 1
            if onlyActivities:
                self.Logger.debug("Only activities allowed, filtering")
                targets = helper.GetActivities(targets)

            if not targets:
                self.Logger.debug("No targets, skipping notification")
                return

            target_count_before_filter = len(targets)
            targets = self._processFilters(targets=targets, event=event)
            ios_targets = helper.GetIosApps(targets)
            activity_targets = helper.GetActivities(targets)
            activity_auto_start_targets = helper.GetActivityAutoStarts(targets) if (event == self.EVENT_STARTED) else []
            android_targets = helper.GetAndroidApps(targets)
            apnsData = self._createActivityStartData(event, state) if event == self.EVENT_STARTED and len(activity_auto_start_targets) > 0 else (self._createApnsPushData(event, state) if len(ios_targets) or len(activity_targets) else None)

            # Some clients might have user interaction disbaled. First send pause so all live activities etc
            if event == self.EVENT_USER_INTERACTION_NEEDED and target_count_before_filter != len(targets):
                self.Logger.info("User interaction needed, first sending pause")
                self.SendNotification(self.EVENT_PAUSED)
                time.sleep(2)

            if len(android_targets) == 0 and apnsData is None:
                self.Logger.info("Skipping push, no Android targets and no APNS data, skipping notification")
                return

            if len(android_targets) == 0 and len(activity_targets) == 0 and (apnsData or {}).get("alert", None) is None:
                self.Logger.info("Skipping push, no Android targets, no iOS targets and APNS data has no alert, skipping notification")
                return

            self._doSendNotification(
                targets=targets,
                highProiroty=not onlyActivities,
                apnsData=apnsData,
                androidData=self._createAndroidPushData(event, state)
            )
        except Exception as e:
            Sentry.ExceptionNoSend("Failed to send notification", e)

        if event in [self.EVENT_DONE, self.EVENT_CANCELLED, self.EVENT_ERROR] and helper is not None:
            helper.RemoveTemporaryApps()

    def _determinePriority(self, event:str, state:Dict[str,Any]):
        if event == self.EVENT_STARTED:
            self.LastProgressUpdate = time.time()
            self.LastProgressPercent = 0
            return 0

        # If the event is not progress, send to all (including time progress)
        elif event != self.EVENT_PROGRESS:
            return 0

        # Sanity check
        elif self.CachedConfig is None:
            self.Logger.warning("No config cached!")
            return 1

        modulus = int(self.CachedConfig["updatePercentModulus"])
        highPrecisionStart = int(self.CachedConfig["highPrecisionRangeStart"])
        highPrecisionEnd = int(self.CachedConfig["highPrecisionRangeEnd"])
        minIntervalSecs = int(self.CachedConfig["minIntervalSecs"])
        time_since_last = time.time() - self.LastProgressUpdate
        progress = int(state[NotificationSender.STATE_PROGRESS_PERCENT])
        if progress < 100 and progress > 0 and (
            (progress % modulus) == 0
            or progress <= highPrecisionStart
            or progress >= (100 - highPrecisionEnd)
        ):
            self.Logger.debug(f"Updating progress in main interval, sending high priotiy update: {progress}")
            self.LastProgressUpdate = time.time()
            self.LastProgressPercent = progress
            return 0
        elif self.LastProgressPercent == 0 and progress != 0:
            self.Logger.debug(f"First progress that is not 0, sending high priority update")
            self.LastProgressPercent = progress
            return 0
        elif time_since_last > minIntervalSecs:
            self.Logger.debug(f"Over {time_since_last} sec passed since last progress update, sending high priority update")
            self.LastProgressUpdate = time.time()
            self.LastProgressPercent = progress
            return 0
        elif time_since_last > (minIntervalSecs / 10):
            self.Logger.debug(f"Over {time_since_last} sec passed since last progress update, sending low priority update")
            return 1
        else:
            self.Logger.debug(f"Skipping progress update, only {time_since_last} seconds passed since last")
            return -1

    def _processFilters(self, targets:List[AppInstance], event:str):
        filterName = None
        if event == self.EVENT_FIRST_LAYER_DONE:
            filterName = "layer_1"
        elif event == self.EVENT_THIRD_LAYER_DONE:
            filterName = "layer_3"
        elif event == self.EVENT_FILAMENT_REQUIRED:
            filterName = "filament_required"
        elif event == self.EVENT_ERROR:
            filterName = "error"
        elif event == self.EVENT_CANCELLED:
            filterName = "cancelled"
        elif event == self.EVENT_USER_INTERACTION_NEEDED:
            filterName = "interaction"
        elif event == self.EVENT_BEEP:
            filterName = "beep"
        else:
            return targets

        return list(filter(lambda target: filterName not in target.ExcludeNotifications, targets))

    def _doSendNotification(self, targets:List[AppInstance], highProiroty:bool, apnsData:Optional[Dict[str,Any]], androidData:str):
        try:
            if len(targets) == 0:
                self.Logger.info("No targets, skipping send")
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

            self.Logger.info(f"Sending notification: {json.dumps(body)}")

            # Make request and check 200
            r = requests.post(
                str(self.CachedConfig["sendNotificationUrl"]),
                timeout=float(10),
                json=body
            )
            function_execution_id = r.headers.get("Function-Execution-Id", "N/A")

            if r.status_code != requests.codes.ok:
                raise Exception(f"Unexpected response code {r.status_code}: {r.text,} (Execution ID: {function_execution_id})")
            else:
                self.Logger.info(f"Send to {len(targets)} was success {r.json()} (Execution ID: {function_execution_id})")

            # Delete invalid tokens
            apps = AppStorageHelper.Get().GetAllApps()
            invalid_tokens = set(r.json()["invalidTokens"])
            stale = [app for app in apps if app.FcmToken in invalid_tokens or app.FcmFallbackToken in invalid_tokens]
            if stale:
                for app in stale:
                    self.Logger.info(f"Removing {app.FcmToken}, no longer valid")
                AppStorageHelper.Get().RemoveApps(stale)

        except Exception as e:
            Sentry.ExceptionNoSend("Failed to send notification %s", e)

    def _createAndroidPushData(self, event:str, state:Dict[str,Any]):
        data = {}
        if event == self.EVENT_BEEP:
            data = { "type": "beep" }
        elif event == self.EVENT_CUSTOM:
            data = { "type": "custom", "message": state.get(self.STATE_CUSTOM_EVENT_MESSAGE, "Gcode notification"), "detail": state.get(self.STATE_CUSTOM_EVENT_DETAIL, None) }
        else:
            eventType = None
            if event == self.EVENT_PROGRESS or event == self.EVENT_STARTED or event == self.EVENT_TIME_PROGRESS or event == self.EVENT_RESUME:
                eventType = "printing"
            elif event == self.EVENT_FIRST_LAYER_DONE:
                eventType = "first_layer_done"
            elif event == self.EVENT_THIRD_LAYER_DONE:
                eventType = "third_layer_done"
            elif event == self.EVENT_PAUSED:
                eventType = "paused"
            elif event == self.EVENT_DONE:
                eventType = "completed"
            elif event == self.EVENT_ERROR:
                eventType = "error"
            elif event == self.EVENT_FILAMENT_REQUIRED:
                eventType = "filament_required"
            elif event == self.EVENT_USER_INTERACTION_NEEDED:
                eventType = "paused_gcode"
            elif event == self.EVENT_MMU2_FILAMENT_START:
                eventType = "mmu_filament_selection_started"
            elif event == self.EVENT_MMU2_FILAMENT_DONE:
                eventType = "mmu_filament_selection_completed"
            elif event == self.EVENT_CANCELLED:
                eventType = "idle"
            elif event == self.EVENT_CUSTOM:
                eventType = "custom"
            else: 
                self.Logger.error(f"Unhandled event: {event}")

            # For errors we forward the error text so the app can show it. This is always set to a
            # non empty value: older plugin versions sent cancels as an error event without any
            # message, so the app uses a missing message to detect them and stay quiet.
            # All other events carry the custom Gcode message, if there is one.
            if event == self.EVENT_ERROR:
                message = state.get(NotificationSender.STATE_ERROR, None) or state.get("Reason", None) or "Print failed"
            else:
                message = state.get(NotificationSender.STATE_CUSTOM_EVENT_MESSAGE, None)

            data = {
                "serverTime": int(time.time()),
                "serverTimePrecise": time.time(),
                "printId": state.get(NotificationSender.STATE_PRINT_ID, None),
                "fileName": state.get(NotificationSender.STATE_FILE_NAME, None),
                "progress": state.get(NotificationSender.STATE_PROGRESS_PERCENT, None),
                "timeLeft": state.get(NotificationSender.STATE_TIME_REMAINING_SEC, None),
                "type": eventType,
                "message": message
            }

        try:
            cipher = AESCipher(AppStorageHelper.Get().GetOrCreateEncryptionKey())
            if cipher.prepare():
                return cipher.encrypt(json.dumps(data))
            else:
                return json.dumps(data)
        except Exception as e:
            Sentry.ExceptionNoSend("Failed to encrypt push notification", e)
            return json.dumps(data)


    def _createApnsPushData(self, event:str, state:Dict[str,Any]) -> Optional[Dict[str,Any]]:
        self.Logger.info(f"Targets contain iOS devices, generating texts for '{event}")
        notificationTitle = None
        notificationBody = None
        notificationTitleKey = None
        notificationTitleArgs = None
        notificationBodyKey = None
        notificationBodyArgs = None
        notificationSound = None
        liveActivityState = None
        defaultBody = f"Time to check {self.PrinterName}!"

        if event == self.EVENT_CUSTOM:
            customDetail = state.get(self.STATE_CUSTOM_EVENT_DETAIL, None)
            skipBody = customDetail == "_skip"
            alert: Dict[str, Any] = {
                "title": state.get(self.STATE_CUSTOM_EVENT_MESSAGE, "Gcode notification"),
            }
            if not skipBody:
                if customDetail:
                    alert["body"] = customDetail
                else:
                    alert["body"] = f"Triggered on {self.PrinterName} by a Gcode command"
                    alert["loc-key"] = "print_notification___custom_message"
                    alert["loc-args"] = [self.PrinterName]
            return {
                "alert": alert,
                "sound": "default",
                "collapseId": "$instanceId-custom",
            }

        elif event == self.EVENT_BEEP:
            return {
                "alert": {
                    "title": "Beep",
                    "body": f"{self.PrinterName} needs attention",
                    "title-loc-key": "print_notification___beep_title",
                    "title-loc-args": [],
                    "loc-key": "print_notification___beep_message",
                    "loc-args": [self.PrinterName]
                },
                "sound": "default",
            }

        elif event == self.EVENT_STARTED:
            notificationTitle = f"{self.PrinterName} started to print"
            notificationTitleKey = "print_notification___start_title"
            notificationTitleArgs = [self.PrinterName]
            notificationBody = "Open the app to see the progress"
            notificationBodyKey = "print_notification___start_message"
            notificationBodyArgs = []
            notificationSound = "default"
            liveActivityState = "printing"

        elif event == self.EVENT_PROGRESS or event == self.EVENT_TIME_PROGRESS or event == self.EVENT_RESUME:
            liveActivityState = "printing"

        elif event == self.EVENT_FIRST_LAYER_DONE:
            notificationTitle = "First layer completed"
            notificationTitleKey = "print_notification___layer_x_completed_title"
            notificationTitleArgs = ["1"]
            notificationBody = defaultBody
            notificationBodyKey = "print_notification___layer_x_completed_message"
            notificationBodyArgs = [self.PrinterName]
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "printing"

        elif event == self.EVENT_THIRD_LAYER_DONE:
            notificationTitle = "Third layer completed"
            notificationTitleKey = "print_notification___layer_x_completed_title"
            notificationTitleArgs = ["3"]
            notificationBody = defaultBody
            notificationBodyKey = "print_notification___layer_x_completed_message"
            notificationBodyArgs = [self.PrinterName]
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "printing"

        elif event == self.EVENT_CANCELLED:
            liveActivityState = "cancelled"
            notificationTitle = f"Print on {self.PrinterName} cancelled"
            notificationTitleKey = "print_notification___cancelled_title"
            notificationTitleArgs = [self.PrinterName]
            notificationBody = state.get(NotificationSender.STATE_FILE_NAME, None)
            notificationBodyKey = state.get(NotificationSender.STATE_FILE_NAME, None)
            notificationBodyArgs = []
            notificationSound = "notification_filament_change.wav"

        elif event == self.EVENT_DONE:
            notificationTitle = f"{self.PrinterName} is done!"
            notificationTitleKey = "print_notification___print_done_title"
            notificationTitleArgs = [self.PrinterName]
            notificationBody = state.get(NotificationSender.STATE_FILE_NAME, None)
            notificationBodyKey = state.get(NotificationSender.STATE_FILE_NAME, None)
            notificationBodyArgs = []
            notificationSound = "notification_print_done.wav"
            liveActivityState = "completed"

        elif event == self.EVENT_FILAMENT_REQUIRED:
            notificationTitle = "Filament required"
            notificationTitleKey = "print_notification___filament_change_required_title"
            notificationTitleArgs =  [self.PrinterName]
            notificationBody = state.get(NotificationSender.STATE_FILE_NAME, None)
            notificationBodyKey = state.get(NotificationSender.STATE_FILE_NAME, None)
            notificationBodyArgs = []
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "filamentRequired"

        elif event == self.EVENT_USER_INTERACTION_NEEDED:
            notificationTitle = f"{self.PrinterName} needs attention!"
            notificationTitleKey = "print_notification___paused_from_gcode_title"
            notificationTitleArgs = [self.PrinterName]
            notificationBody = "Print was paused"
            notificationBodyKey = "print_notification___paused_from_gcode_message"
            notificationBodyArgs = []
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "pausedGcode"

        elif event == self.EVENT_PAUSED:
            liveActivityState = "paused"

        elif event == self.EVENT_MMU2_FILAMENT_START:
            notificationTitle = f"{self.PrinterName} asks for filament selection"
            notificationTitleKey = "print_notification___filament_selection_title"
            notificationTitleArgs = [self.PrinterName]
            notificationBody = "Print is waiting for MMU"
            notificationBodyKey = "print_notification___filament_selection_message"
            notificationBodyArgs = []
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "filamentRequired"

        elif event == self.EVENT_MMU2_FILAMENT_DONE:
            liveActivityState = "printing"

        elif event == self.EVENT_ERROR:
            notificationTitle = f"{self.PrinterName} needs attention!"
            notificationTitleKey = "print_notification___paused_from_gcode_title"
            notificationTitleArgs = [self.PrinterName]
            notificationBody = state.get(self.STATE_ERROR, "Print failed")
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "error"

        else:
            self.Logger.warning(f"Missing handling for '{event}'")
            return None

        # Let's only end the activity on cancel. If we end it on completed the alert isn't shown
        data = self._createActivityContentState(
            isEnd=event == self.EVENT_CANCELLED or event == self.EVENT_ERROR or event == self.EVENT_DONE,
            state=state,
            liveActivityState=liveActivityState
        )

        # Delay cancel or complete notification to ensure it's last
        if event == self.EVENT_CANCELLED or event == self.EVENT_DONE or event == self.EVENT_ERROR:
            time.sleep(5)

        if notificationSound is not None:
            data["sound"] = notificationSound

        if notificationBody is None and notificationBodyKey is None:
            notificationBody = f"Time to check {self.PrinterName}!"

        if notificationTitle is not None or notificationTitleKey is not None:
             # Create alert
            data["alert"] = {
                "title": notificationTitle,
                "body": notificationBody,
                "title-loc-key": notificationTitleKey,
                "title-loc-args": notificationTitleArgs,
                "loc-key": notificationBodyKey,
                "loc-args": notificationBodyArgs
            }

            data["activity-alert"] = {
                "title": {
                    "loc-key": notificationTitleKey,
                    "loc-args": notificationTitleArgs,
                },
                "body": {
                    "loc-key": notificationBodyKey,
                    "loc-args": notificationBodyArgs,
                },
                # "sound": notificationSound -> We send a notification alongside because iOS doesn't play this sound reliably, especially with Apple Watch connected
            }

            # Delete None values, causes issues with APNS
            for k, v in dict(data["alert"]).items():
                if v is None or (isinstance(v, list) and len(v) == 0):
                    del data["alert"][k]

        return data


    def _createActivityStartData(self, event:str, state:Dict[str,Any]) -> Dict[str,Any]:
        # Base: Activity state
        data = self._createActivityContentState(
            isEnd=False,
            state=state,
            liveActivityState="printing"
        )
        # Add alert
        notification = self._createApnsPushData(event, state)
        if notification is not None:
            data.update(notification)

        # Add attributes needed for start
        # ! the node JS server will set attributes.instanceId
        data.update(
            {
                "event": "start",
                "attributes-type": "PrintActivityAttributes",
                "attributes": {
                    "filePath": state.get(NotificationSender.STATE_FILE_PATH, None),
                    "startedAt": time.time()
                }
            }
        )
        return data


    def _createActivityRenewData(self, state:Dict[str,Any]) -> Dict[str,Any]:
        # Silent push-to-start used to replace an activity that is about to expire.
        # Same payload as start, but without an alert so the user sees no notification.
        data = self._createActivityContentState(
            isEnd=False,
            state=state,
            liveActivityState="printing"
        )
        data.update(
            {
                "event": "start",
                "attributes-type": "PrintActivityAttributes",
                "attributes": {
                    "filePath": state.get(NotificationSender.STATE_FILE_PATH, None),
                    "startedAt": time.time()
                }
            }
        )
        return data


    def _createActivityContentState(self, isEnd:bool, state:Dict[str,Any], liveActivityState:str) -> Dict[str,Any]:
        return {
            "event": "end" if isEnd else "update",
            "content-state": {
                "fileName": state.get(NotificationSender.STATE_FILE_NAME, None),
                "filePath": state.get(NotificationSender.STATE_FILE_PATH, None),
                "progress": int(float(state.get(NotificationSender.STATE_PROGRESS_PERCENT, 0))),
                "sourceTime": int(time.time() * 1000),
                "state": liveActivityState,
                "error": state.get(self.STATE_ERROR, None),
                "timeLeft": int(float(state.get(NotificationSender.STATE_TIME_REMAINING_SEC, 0))),
                "printTime": int(float(state.get(NotificationSender.STATE_DURATION_SEC, 0))),
            }
        }

    def _getPushTargets(self, event:str):
        self.Logger.info(f"Finding targets for event={event}")
        helper = AppStorageHelper.Get()
        apps = helper.GetAllApps()
        phones:Dict[str, List[AppInstance]] = {}

        # Group all apps by phone
        for app in apps:
            instance_id = app.InstanceId
            phone = phones.get(instance_id, [])
            phone.append(app)
            phones[instance_id] = phone

        # Pick activity if available, otherwise any other app
        def pick_best_app(apps: List[AppInstance]):
            activities = helper.GetActivities(apps)
            ios = helper.GetIosApps(apps)
            android = helper.GetAndroidApps(apps)
            hasActivityAutoStart = False

            # For start events we can generate LiveActivity instances on the fly which will start a LiveActivity
            if event == self.EVENT_STARTED:
                for ios_app in ios:
                    if ios_app.ActivityAutoStartToken:
                        hasActivityAutoStart = True
                        activities.append(ios_app.WithToken(ios_app.ActivityAutoStartToken))

            if len(android):
                # If we have android...return any way. Handled all the same.
                return android
            elif event in [self.EVENT_CUSTOM, self.EVENT_BEEP, self.EVENT_FIRST_LAYER_DONE, self.EVENT_THIRD_LAYER_DONE]:
                # If we have an event Live Activities can't handle send via notification
                return ios
            elif event == self.EVENT_STARTED and hasActivityAutoStart:
                # We can start the activity automatically, only push to Activity
                return activities
            elif event in [self.EVENT_FILAMENT_REQUIRED, self.EVENT_USER_INTERACTION_NEEDED, self.EVENT_CANCELLED, self.EVENT_DONE, self.EVENT_ERROR]:
                # If we have a important event, send to all targets
                return activities + ios
            else:
                # Send only to activities, might be empty
                return activities

        # Get apps per phone and flatten
        apps = [pick_best_app(phone) for phone in phones.values()]
        apps = [app for sublist in apps for app in sublist]
        return list(filter(lambda app: app is not None, apps))


    def _continuouslyCheckActivitiesExpired(self):
        t = threading.Thread(
            target=self._doContinuouslyCheckActivitiesExpired,
            args=[]
        )
        t.daemon = True
        t.start()


    # How often the expiry/renew loop runs.
    ACTIVITY_CHECK_INTERVAL_SEC = 60
    # How long before ExpireAt we try to seamlessly replace an activity with a fresh one.
    ACTIVITY_RENEW_MARGIN_SEC = 1800

    def _doContinuouslyCheckActivitiesExpired(self):
        self.Logger.debug(f"Checking for expired apps every {self.ACTIVITY_CHECK_INTERVAL_SEC}s")
        while True:
            time.sleep(self.ACTIVITY_CHECK_INTERVAL_SEC)

            try:
                helper = AppStorageHelper.Get()
                all_apps = helper.GetAllApps()

                # On iOS 17.2+ devices we hold a push-to-start token. Before an activity
                # expires we start a fresh activity on the same instance, so the user never
                # sees the "expired" state. The new activity registers its own update token
                # and ExpireAt via the app, so we stop tracking the old record here.
                self._renewExpiringActivities(helper, all_apps)

                # Reload, the renew step may have removed activity records.
                expired = helper.GetExpiredApps(helper.GetAllApps())
                if len(expired):
                    self.Logger.debug(f"Found {len(expired)} expired apps")
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
                    self.Logger.debug("Cleaned up expired apps")


            except Exception as e:
                Sentry.ExceptionNoSend("Failed to retire expired", e)


    def _renewExpiringActivities(self, helper:AppStorageHelper, all_apps:List[AppInstance]):
        now = time.time()

        # Map instanceId -> push-to-start token, newest first wins.
        auto_start_by_instance:Dict[str,str] = {}
        for app in helper.GetActivityAutoStarts(all_apps):
            if app.ActivityAutoStartToken is not None:
                auto_start_by_instance.setdefault(app.InstanceId, app.ActivityAutoStartToken)

        # Find activities entering the renew window that we can still replace.
        renewable:List[AppInstance] = []
        for activity in helper.GetActivities(all_apps):
            if activity.ExpireAt is None:
                continue
            if now < activity.ExpireAt - self.ACTIVITY_RENEW_MARGIN_SEC:
                continue  # not close enough to expiry yet
            if now > activity.ExpireAt:
                continue  # already expired, handled by the expired path
            if activity.InstanceId not in auto_start_by_instance:
                continue  # no push-to-start token, can't renew (older iOS)
            renewable.append(activity)

        if not renewable:
            return

        self.Logger.info(f"Renewing {len(renewable)} live activities before expiry")
        apnsData = self._createActivityRenewData(self.LastPrintState)

        for activity in renewable:
            token = auto_start_by_instance[activity.InstanceId]
            # Start a fresh activity via push-to-start on the same instance.
            self._doSendNotification(
                targets=[activity.WithToken(token)],
                highProiroty=True,
                apnsData=apnsData,
                androidData="none"
            )

        # Stop tracking the old records. Progress updates now target the new activity
        # once the app reports its update token; the old activity dies at Apple's limit.
        helper.RemoveApps(renewable)


    #
    # CONFIG
    #

    def _continuouslyUpdateConfig(self):
        self.Logger.info("Updating config")
        t = threading.Thread(target=self._doContinuouslyUpdateConfig)
        t.daemon = True
        t.start()

    def _doContinuouslyUpdateConfig(self):
        while True:
            time.sleep(3600)
            # If we have no config cached or the cache is older than a day, request new config
            cache_config_max_age = time.time() - 86400
            if self.CachedConfigAt > cache_config_max_age:
                self.Logger.info("Config still valid")
                continue

            # Request config, fall back to default
            try:
                r = requests.get(
                    "https://www.octoapp.eu/config/plugin.json", timeout=float(15)
                )
                if r.status_code != requests.codes.ok:
                    raise Exception(f"Unexpected response code {r.status_code}")
                self.CachedConfig = r.json()
                self.CachedConfigAt = time.time()

                self.Logger.info(f"OctoApp loaded config: {self.CachedConfig}")
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to fetch config using defaults for 5 minutes", e)
                self.CachedConfig = self.DefaultConfig
                self.CachedConfigAt = cache_config_max_age + 300

# pylint: disable=all
class AESCipher:
    _ready = None

    def __init__(self, key:str):
        self.key = hashlib.sha256(key.encode()).digest()

    def prepare(self) -> bool:
        global AES, Random

        if AESCipher._ready is None:
            try:
                from Crypto import Random
                from Crypto.Cipher import AES
                AESCipher._ready = True
            except ImportError:
                Sentry.LogError("Missing Crypto, notifications will not be encrypted. This happens on Sonic Pad and K1 (maybe others)")
                AESCipher._ready = False

        return AESCipher._ready

    def encrypt(self, raw:str):
        global AES, Random
        bs = AES.block_size

        def _pad(s:str):
            return s + (bs - len(s) % bs) * chr(bs - len(s) % bs)

        raw = _pad(raw)
        iv = Random.new().read(bs)
        cipher = AES.new(self.key, AES.MODE_CBC, iv) # type: ignore
        return base64.b64encode(iv + cipher.encrypt(raw.encode())).decode("utf-8")
