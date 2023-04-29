
from .sub_plugin import OctoAppSubPlugin

import threading
from Crypto import Random
from Crypto.Cipher import AES
import requests
import base64
import hashlib
import json
import time
import flask
from octoprint.access.permissions import Permissions
from octoprint.events import Events
import os
import uuid

class OctoAppNotificationsSubPlugin(OctoAppSubPlugin):

    EVENT_PAUSED="paused"
    EVENT_PAUSED_GCODE="paused_gcode"
    EVENT_FILAMENT_REQUIRED="filament_required"
    EVENT_COMPLETED="completed"
    EVENT_CANCELLED="cancelled"
    EVENT_PRINTING="printing"
    EVENT_MMU2_FILAMENT_START="mmu_filament_selection_started"
    EVENT_MMU2_FILAMENT_DONE="mmu_filament_selection_completed"
    EVENT_IDLE="idle"
    EVENT_BEEP="beep"

    def __init__(self, parent):
        super().__init__(parent)
        self.data_file = None
        self.print_state = {}
        self.last_progress_update = 0

    def on_after_startup(self):
        self.data_file = os.path.join(self.parent.get_plugin_data_folder(), "apps.json")
        self._logger.info("NOTIFICATION | Using config file %s" % self.data_file )
        self.upgrade_data_structure()
        self.upgrade_expiration_date()
        self.remove_temporary_apps()
        self.get_or_create_encryption_key()
        self.last_event = None
        self.continuously_check_activities_expired()
    
    def on_api_command(self, command, data):
        if command == "registerForNotifications":
            if not Permissions.PLUGIN_OCTOAPP_RECEIVE_NOTIFICATIONS.can():
                return flask.make_response("Insufficient rights", 403)

            fcmToken = data["fcmToken"]

            # if this is a temporary app, remove all other temp apps for this instance
            instnace_id = data.get("instanceId", None)
            if fcmToken.startswith("activity:") and instnace_id is not None:
                self.remove_temporary_apps(for_instance_id=instnace_id)

            # load apps and filter the given FCM token out
            apps = self.get_apps()
            if apps:
                apps = [app for app in apps if app["fcmToken"] != fcmToken]
            else:
                apps = []

            # add app for new registration
            apps.append(
                dict(
                    fcmToken=fcmToken,
                    fcmTokenFallback=data.get("fcmTokenFallback", None),
                    instanceId=data["instanceId"],
                    displayName=data["displayName"],
                    displayDescription=data.get("displayDescription", None),
                    model=data["model"],
                    appVersion=data["appVersion"],
                    appBuild=data["appBuild"],
                    appLanguage=data["appLanguage"],
                    lastSeenAt=time.time(),
                    expireAt=(time.time() + data["expireInSecs"]) if "expireInSecs" in data else self.get_default_expiration_from_now(),
                )
            )

            # save
            self._logger.info("NOTIFICATION | Registered app %s" % fcmToken)
            self.set_apps(apps)
            self.log_apps()
            self.parent._settings.save()
            return flask.jsonify(dict())
        
        else: 
            return None
    
    def get_default_expiration_from_now(self):
        return (time.time() + 2592000)

    def update_print_state(self):
        progress = self.parent._printer.get_current_data()["progress"]
        self.print_state["progress"] = int(progress["completion"])
        self.print_state["time_left"] = progress["printTimeLeft"]
        self.print_state["print_time"] = progress["printTime"]

    def on_print_progress(self, storage, path, progress):
        self.update_print_state()

        # send update, but don't send for 100%
        # we send updated in "modulus" interval as well as for the first and last "modulus" percent
        config = self.config
        modulus = config["updatePercentModulus"]
        highPrecisionStart = config["highPrecisionRangeStart"]
        highPrecisionEnd = config["highPrecisionRangeEnd"]
        time_since_last = time.time() - self.last_progress_update
        if progress < 100 and progress > 0 and (
            (progress % modulus) == 0
            or progress <= highPrecisionStart
            or progress >= (100 - highPrecisionEnd)
        ):
            self._logger.debug("NOTIFICATION | Updating progress in main interval %s" % self.last_event)
            self.send_notification(event=self.EVENT_PRINTING)
            self.last_progress_update = time.time()
        elif time_since_last > 300:
            self._logger.debug("NOTIFICATION | Over %s sec passed since last progress update, sending low priority update" % int(time_since_last))
            self.send_notification(event=self.EVENT_PRINTING, only_activities=True)
            self.last_progress_update = time.time()
        else:
            self._logger.debug("NOTIFICATION | Skipping progress update, only %s seconds passed since last" % int(time_since_last))


    def on_event(self, event, payload):
        # Plugin not ready yet?
        if (self.data_file == None): return

        if event == Events.SHUTDOWN:
            # Blocking send to guarantee completion before shutdown
            # This notification ensures all progress notifications will be closed
            if self.print_state.get("progress", None) is not None:
                self.send_notification_blocking(self.EVENT_CANCELLED, state=self.print_state, only_activities=False)
            else:
                self.send_notification_blocking(self.EVENT_IDLE, state=self.print_state, only_activities=False)

        elif event == Events.PRINT_STARTED:
            self.last_progress_update = 0
            self.print_state = dict(
                name=payload["name"],
                id=str(uuid.uuid4()),
                progress=0,
                time_left=self.parent._printer.get_current_data()["progress"]["printTimeLeft"]
            )

            self.send_notification(event=self.EVENT_PRINTING)

        elif event == Events.PRINT_RESUMED:
            self.update_print_state()
            self.send_notification(event=self.EVENT_PRINTING)

        elif event == Events.PRINT_DONE:
            self.print_state["progress"] = 100
            self.send_notification(event=self.EVENT_COMPLETED)
            self.print_state = {}

        elif event == Events.PRINT_FAILED or event == Events.PRINT_CANCELLED:
            # This is called twice when cancelled
            if self.print_state.get("id", None) is not None:
                self.send_notification(event=self.EVENT_CANCELLED)
                self.print_state = {}

        elif event == Events.FILAMENT_CHANGE and self.print_state.get("progress", None) is not None:
            self.update_print_state()
            self.send_notification(event=self.EVENT_FILAMENT_REQUIRED)

        elif event == Events.CLIENT_OPENED:
            self.send_settings_plugin_message(self.get_apps())

        elif event == Events.PRINT_PAUSED:
            # Pause is triggered after FILAMENT_CHANGE, prevent duplicate events
            self.update_print_state()
            if self.last_event != self.EVENT_FILAMENT_REQUIRED and self.last_event != self.EVENT_PAUSED:
                self._logger.debug("NOTIFICATION | Preparing pause notification, last event was %s" % self.last_event)
                self.send_notification(event=self.EVENT_PAUSED)

    def on_gcode_send(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        if gcode == "M300":
            self.update_print_state()
            time_left = self.print_state["time_left"]
            progress = self.print_state["progress"]
            if time_left > 30 or progress < 95:
                self._logger.debug("NOTIFICATION | Performing beep, %s seconds left and %s percent" % (time_left, progress))    
                self.send_notification(event=self.EVENT_BEEP)
            else:
                self._logger.debug("NOTIFICATION | Skipping beep, only %s seconds left and %s percent" % (time_left, progress))    
    
        elif gcode == "M601" or gcode == "@pause":
            self.update_print_state()
            self.send_notification(event=self.EVENT_PAUSED_GCODE)

    #
    # NOTIFICATIONS
    #


    def send_notification(self, event, only_activities=False):
        self.last_event = event
        t = threading.Thread(
            target=self.send_notification_blocking,
            args=[event, self.print_state.copy(), only_activities]
        )
        t.daemon = True
        t.start()


    def send_notification_blocking(self, event, state, only_activities):
        try:
            self._logger.info("NOTIFICATION | Preparing notification for %s" % event)
            targets = self.get_push_targets(
                preferActivity=self.should_prefer_activity(event),
                canUseNonActivity=self.can_use_non_activity(event) and not only_activities
            )

            if only_activities:
                self._logger.debug("NOTIFICATION | Only activities allowed, filtering")
                targets = self.get_activities(targets)

            if not targets:
                self._logger.debug("NOTIFICATION | No targets, skipping notification")
                return

            ios_targets = self.get_ios_apps(targets)
            activity_targets = self.get_activities(targets)
            android_targets = self.get_android_apps(targets)
            apnsData = self.createApnsPushData(event, state) if len(ios_targets) or len(activity_targets) else None

            if not len(android_targets) and apnsData is None:
                self._logger.info("NOTIFICATION | Skipping push, no Android targets and no APNS data, skipping notification")
                return
            
            if not len(android_targets) and not len(activity_targets) and apnsData.get("alert", None) is None:
                self._logger.info("NOTIFICATION | Skipping push, no Android targets, no iOS targets and APNS data has no alert, skipping notification")
                return

            self.send_notification_blocking_raw(
                targets=targets,
                high_priority=not only_activities,
                apnsData=apnsData,
                androidData=self.createAndroidPushData(event, state)
            )

            # Remove temporary apps after getting targets
            if event == self.EVENT_CANCELLED or event == self.EVENT_COMPLETED:
                self.remove_temporary_apps()
        except Exception as e:
            self._logger.debug("NOTIFICATION | Failed to send notification %s", e, exc_info=True)


    def send_notification_blocking_raw(self, targets, high_priority, apnsData, androidData):
        try:
            if not len(targets): return
            config = self.config

            # Base priority on only_activities. If the flag is set this is a low
            # priority status update
            body = dict(
                targets=list(map(lambda x: {
                    "fcmToken": x["fcmToken"],
                    "fcmTokenFallback": x.get("fcmTokenFallback", None),
                    "instanceId": x["instanceId"]
                }, targets)),
                highPriority=high_priority,
                androidData=androidData,
                apnsData=apnsData,
            )

            self._logger.debug("NOTIFICATION | Sending notification: %s", json.dumps(body))

            # Make request and check 200
            r = requests.post(
                config["sendNotificationUrl"],
                timeout=float(10), 
                json=body
            )
            if r.status_code != requests.codes.ok:
                raise Exception("Unexpected response code %d" % r.status_code)
            else:
                self._logger.info("NOTIFICATION | Send was success")

            # Delete invalid tokens
            apps = self.get_apps()
            invalid_tokens = r.json()["invalidTokens"]
            for fcmToken in invalid_tokens:
                self._logger.info("NOTIFICATION | Removing %s, no longer valid" % fcmToken)
                apps = [app for app in apps if app["fcmToken"] != fcmToken]
            self.set_apps(apps)
            self.parent._settings.save()
            self.log_apps()

        except Exception as e:
            self._logger.warn("NOTIFICATION | Failed to send notification %s", e, exc_info=True)


    def createAndroidPushData(self, event, state):
        cipher = AESCipher(self.get_or_create_encryption_key())
        if event == self.EVENT_BEEP:
            data = {}
        else:
            type = None
            if event == self.EVENT_PRINTING:
                type = "printing"
            elif event == self.EVENT_PAUSED:
                type = "paused"
            elif event == self.EVENT_PAUSED_GCODE:
                type = "paused_gcode"
            elif event == self.EVENT_COMPLETED:
                type = "completed"
            elif event == self.EVENT_FILAMENT_REQUIRED:
                type = "filament_required"
            elif event == self.EVENT_MMU2_FILAMENT_START:
                type = "mmu_filament_selection_started"
            elif event == self.EVENT_MMU2_FILAMENT_DONE:
                type = "mmu_filament_selection_completed"
            elif event == self.EVENT_CANCELLED or event == self.EVENT_IDLE:
                type = "idle"

            data = {
                "serverTime": int(time.time()),
                "serverTimePrecise": time.time(),
                "printId": state.get("id", None),
                "fileName": state.get("name", None),
                "progress": state.get("progress", None),
                "timeLeft": state.get("time_left", None),
                "type": type
            }
        return cipher.encrypt(json.dumps(data))

    def createApnsPushData(self, event, state):
        self._logger.debug("NOTIFICATION | Targets contain iOS devices, generating texts for '%s'", event)
        notificationTitle = None
        notificationSound = None
        liveActivityState = None

        if event == self.EVENT_BEEP:
            return {
                "alert": {
                    "title": "Beep!",
                    "body": "Your printer needs attention",
                },
                "sound": "default",
            }

        elif event == self.EVENT_IDLE:
            # Not supported
            return None

        elif event == self.EVENT_PRINTING:
            liveActivityState = "printing"

        elif event == self.EVENT_CANCELLED:
            liveActivityState = "cancelled"

        elif event == self.EVENT_COMPLETED:
            notificationTitle = "Print completed"
            notificationSound = "notification_print_done.wav"
            liveActivityState = "completed"

        elif event == self.EVENT_FILAMENT_REQUIRED:
            notificationTitle = "Filament required"
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "filamentRequired"

        elif event == self.EVENT_PAUSED:
            liveActivityState = "paused"

        elif event == self.EVENT_PAUSED_GCODE:
            notificationTitle = "Print paused by Gcode"
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "paused_gcode"
        
        elif event == self.EVENT_MMU2_FILAMENT_START:
            notificationTitle = "MMU2 filament selection required"
            notificationSound = "notification_filament_change.wav"
            liveActivityState = "filamentRequired"

        elif event == self.EVENT_MMU2_FILAMENT_DONE:
            liveActivityState = "printing"

        else:
            self._logger.warn("NOTIFICATION | Missing handling for '%s'" % event)
            return None

        # Let's only end the activity on cancel. If we end it on completed the alert isn't shown
        data = self.create_activity_content_state(
            is_end=event == self.EVENT_CANCELLED,
            state=state,
            liveActivityState=liveActivityState
        )

        # Delay cancel or complete notification to ensure it's last
        if event == self.EVENT_CANCELLED or event == self.EVENT_COMPLETED:
            time.sleep(5)

        if notificationSound is not None:
            data["sound"] = notificationSound

        if notificationTitle is not None:
            data["alert"] = {
                "title": notificationTitle,
                "body": state.get("name", "???"),
            }

        return data

    def create_activity_content_state(self, is_end, state, liveActivityState):
        return {
            "event": "end" if is_end else "update",
            "content-state": {
                "fileName": state.get("name", None),
                "progress":state.get("progress", None),
                "sourceTime": int(time.time() * 1000),
                "state": liveActivityState,
                "timeLeft": state.get("time_left", None),
                "printTime": state.get("print_time", None),
            }
        }

    def should_prefer_activity(self, event):
        return event != self.EVENT_BEEP

    def can_use_non_activity(self, event):
        return event != self.EVENT_PRINTING

    def get_push_targets(self, preferActivity, canUseNonActivity):
        self._logger.debug("NOTIFICATION | Finding targets preferActivity=%s canUseNonActivity=%s" % (preferActivity, canUseNonActivity))
        apps = self.get_apps()
        phones = {}

        # Group all apps by phone
        for app in apps:
            instance_id = app["instanceId"]
            phone = phones.get(instance_id, [])
            phone.append(app)
            phones[instance_id] = phone

        # Pick activity if available, otherwise any other app
        def pick_best_app(apps):
            activities = self.get_activities(apps)
            ios = self.get_ios_apps(apps)
            android = self.get_android_apps(apps)
            if len(activities) and preferActivity:
                return activities[0]
            elif len(ios) and canUseNonActivity:
                return ios[0]
            elif len(android):
                return android[0]
            else:
                return None

        apps = list(map(lambda phone: pick_best_app(phone), phones.values()))
        return list(filter(lambda app: app is not None, apps))


    #
    # APPS
    #

    def continuously_check_activities_expired(self):
        t = threading.Thread(
            target=self.do_continuously_check_activities_expired,
            args=[]
        )
        t.daemon = True
        t.start()

    def do_continuously_check_activities_expired(self):
         self._logger.debug("NOTIFICATION | Checking for expired apps every 60s")
         while True:
            time.sleep(60)

            try:
                expired = self.get_expired_apps(self.get_activities(self.get_apps()))
                if len(expired):
                    self._logger.debug("NOTIFICATION | Found %s expired apps" % len(expired))
                    self.log_apps()

                    expired_activities = self.get_activities(expired)
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

                    filtered_apps = list(filter(lambda app: any(app["fcmToken"] != x["fcmToken"] for x in expired), self.get_apps()))
                    self.set_apps(filtered_apps)
                    self.log_apps()
                    self._logger.debug("NOTIFICATION | Cleaned up expired apps")


            except Exception as e:
                self._logger.debug("NOTIFICATION | Failed to retire expired %s", e, exc_info=True)


    def get_android_apps(self, apps):
        return list(filter(lambda app: not app["fcmToken"].startswith("activity:") and not app["fcmToken"].startswith("ios:"), apps))

    def get_expired_apps(self, apps):
        return list(filter(lambda app: app["expireAt"] is not None and time.time() > app["expireAt"], apps))

    def get_ios_apps(self, apps):
        return list(filter(lambda app: app["fcmToken"].startswith("ios:"), apps))

    def get_activities(self, apps):
        return list(filter(lambda app: app["fcmToken"].startswith("activity:"), apps))

    def log_apps(self):
        apps = self.get_apps()
        self._logger.debug("NOTIFICATION | Now %s apps registered" % len(apps))
        for app in apps:
            self._logger.debug("NOTIFICATION |    => %s" % app["fcmToken"][0:100])

    def upgrade_data_structure(self):
        try:
            if not os.path.isfile(self.data_file):
                self._logger.debug("NOTIFICATION | Updating data structure to: %s" %
                                self.data_file)
                apps = self.parent._settings.get(["registeredApps"])
                self.set_apps(apps)
                self._logger.debug("NOTIFICATION | Saved data to: %s" % self.data_file)
                self.parent._settings.remove(["registeredApps"])
        except Exception as e:
             self._logger.error("NOTIFICATION | Failed to upgrade data structure: %s" , e, exc_info=True)

    def upgrade_expiration_date(self):
        try:
            def add_expiration(app):
                app["expireAt"] = app.get("expireAt", None) or self.get_default_expiration_from_now()
                return app

            apps = self.get_apps()
            apps = list(map(lambda app: add_expiration(app), apps))
            self.set_apps(apps)
        except Exception as e:
             self._logger.error("NOTIFICATION | Failed to upgrade expiration: %s" , e, exc_info=True)

    def get_apps(self):
        try: 
            if os.path.isfile(self.data_file):
                with open(self.data_file, 'r') as file:
                    apps = json.load(file)
                if apps is None:
                    apps = []
                return apps
            else:
                return []
        except Exception as e: 
            self._logger.debug("NOTIFICATION | Failed to load apps %s" , e, exc_info=True)
            return []

    def remove_temporary_apps(self, for_instance_id=None):
        apps = self.get_apps()
        
        if for_instance_id is None:
            apps = list(filter(lambda app: not app["fcmToken"].startswith("activity:") ,apps))
            self._logger.debug("NOTIFICATION | Removed all temporary apps")
        else:
            apps = list(filter(lambda app: not app["fcmToken"].startswith("activity:") or app["instanceId"] != for_instance_id ,apps))
            self._logger.debug("NOTIFICATION | Removed all temporary apps for %s" % for_instance_id)

        self.set_apps(apps)

    def set_apps(self, apps):
        with open(self.data_file, 'w') as outfile:
            json.dump(apps, outfile)
        self.send_settings_plugin_message(apps)

    def send_settings_plugin_message(self, apps):
        mapped_apps = list(map(lambda x: dict(
            displayName=x.get("displayName", None),
            lastSeenAt=x.get("lastSeenAt", None),
            expireAt=x.get("expireAt", None),
            displayDescription=x.get("displayDescription", None)
        ), apps))
        mapped_apps = sorted(mapped_apps, key=lambda d: d.get("expireAt", None) or float('inf'))
        self.parent._plugin_manager.send_plugin_message(
            "%s.settings" % self.parent._identifier, {"apps": mapped_apps})

    #
    # SETTINGS
    #


    def get_or_create_encryption_key(self):
        key = self.parent._settings.get(["encryptionKey"])
        if key is None:
            key = str(uuid.uuid4())
            self._logger.info("NOTIFICATION | Created new encryption key")
            self.parent._settings.set(["encryptionKey"], key)
        return key


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
