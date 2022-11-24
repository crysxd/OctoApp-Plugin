
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


    def __init__(self, parent):
        super().__init__(parent)
        self.last_progress_notification_at = 0
        self.last_progress = None
        self.last_print_name = None
        self.data_file = None
    

    def send_special_notification(self, type):
        self.send_notification(
            dict(
                type=type,
                fileName=self.last_print_name,
                progress=self.last_progress,
                timeLeft=self.last_time_left,
            ),
            True,
        )


    def on_after_startup(self):
        self.data_file = os.path.join(self.parent.get_plugin_data_folder(), "apps.json")
        self.update_data_structure()
        self.get_or_create_encryption_key()
        
    
    def on_api_command(self, command, data):
        if command == "registerForNotifications":
            if not Permissions.PLUGIN_OCTOAPP_RECEIVE_NOTIFICATIONS.can():
                return flask.make_response("Insufficient rights", 403)

            fcmToken = data["fcmToken"]

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
                    instanceId=data["instanceId"],
                    displayName=data["displayName"],
                    model=data["model"],
                    appVersion=data["appVersion"],
                    appBuild=data["appBuild"],
                    appLanguage=data["appLanguage"],
                    lastSeenAt=time.time(),
                )
            )

            # save
            self._logger.info("Registered app %s" % fcmToken)
            self._logger.debug("registered apps %s" % apps)
            self.set_apps(apps)
            self.parent._settings.save()
            return flask.jsonify(dict())
        
        else: 
            return None


    def on_print_progress(self, storage, path, progress):
        self.last_progress = progress
        self.last_time_left = self.parent._printer.get_current_data()["progress"]["printTimeLeft"]

        # send update, but don't send for 100%
        # we send updated in "modulus" interval as well as for the first and last "modulus" percent
        config = self.config
        modulus = config["updatePercentModulus"]
        highPrecisionStart = config["highPrecisionRangeStart"]
        highPrecisionEnd = config["highPrecisionRangeEnd"]
        if progress < 100 and (
            (progress % modulus) == 0
            or progress <= highPrecisionStart
            or progress >= (100 - highPrecisionEnd)
        ):
            self.send_notification(
                dict(
                    type="printing",
                    fileName=self.last_print_name,
                    progress=self.last_progress,
                    timeLeft=self.last_time_left,
                ),
                True,
            )


    def on_event(self, event, payload):
        # Plugin not ready yet?
        if (self.data_file == None): return

        if event == Events.SHUTDOWN:
            # Blocking send to guarantee completion before shutdown
            # This notification ensures all progress notifications will be closed
            self.send_notification_blocking(
                dict(type="idle"),
                True
            )

        elif event == Events.PRINT_STARTED:
            self.last_print_name = payload["name"]
            self.last_progress_notification_at = 0

        elif event == Events.PRINT_RESUMED:
            self.send_notification(
            dict(
                type="printing",
                fileName=self.last_print_name,
                progress=self.last_progress,
                timeLeft=self.last_time_left,
            ),
            True,
        )

        elif event == Events.PRINT_DONE:
            self.last_progress = None
            self.last_print_name = None
            self.last_time_left = None
            self.send_notification(
                dict(type="completed", fileName=payload["name"]),
                True
            )

        elif event == Events.PRINT_FAILED or event == Events.PRINT_CANCELLED:
            self.last_progress = None
            self.last_print_name = None
            self.send_notification(
                dict(type="idle", fileName=payload["name"]),
                True
            )

        elif event == Events.FILAMENT_CHANGE and self.last_progress is not None:
            self.send_notification(
                dict(
                    type="filament_required",
                    fileName=self.last_print_name,
                    progress=self.last_progress,
                    timeLeft=self.last_time_left,
                ),
                True,
            )

        elif event == Events.CLIENT_OPENED:
            self.send_settings_plugin_message(self.get_apps())

        elif event == Events.PRINT_PAUSED:
            self.send_notification(
                dict(
                    type="paused",
                    fileName=payload["name"],
                    progress=self.last_progress,
                    timeLeft=self.last_time_left,
                ),
                True,
            )


    def on_gcode_send(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        if gcode == "M300":
            self.send_notification(
                dict(type="beep"),
                True,
            )

        elif gcode == "M601":
            self.send_notification(
                dict(
                    type="paused_gcode",
                    fileName=self.last_print_name,
                    progress=self.last_progress,
                    timeLeft=self.last_time_left,
                ),
                True,
            )


    #
    # NOTIFICATIONS
    #


    def send_notification(self, data, highPriority):
        t = threading.Thread(
            target=self.send_notification_blocking,
            args=[data, highPriority]
        )
        t.daemon = True
        t.start()


    def send_notification_blocking(self, data, highPriority):
        try:
            config = self.config

            # encrypt message and build request body
            data["serverTime"] = int(time.time())
            data["serverTimePrecise"] = time.time()
            self._logger.debug("Sending notification %s" % data)
            cipher = AESCipher(self.get_or_create_encryption_key())
            data = cipher.encrypt(json.dumps(data))
            apps = self.get_apps()
            if not apps:
                self._logger.debug("No apps registered, skipping notification")
                return

            body = dict(targets=apps, highPriority=highPriority, data=data)

            # make request and check 200
            r = requests.post(config["sendNotificationUrl"],
                              timeout=float(10), json=body)
            if r.status_code != requests.codes.ok:
                raise Exception("Unexpected response code %d" % r.status_code)
            else:
                self._logger.debug("Send was success")

            # delete invalid tokens
            apps = self.get_apps()
            for fcmToken in r.json()["invalidTokens"]:
                apps = [app for app in apps if app["fcmToken"] != fcmToken]
            self.set_apps(apps)
            self.parent._settings.save()
            self._logger.debug("Updated apps" )
        except Exception as e:
            self._logger.debug("Failed to send notification %s" % e)


    #
    # APPS
    #


    def update_data_structure(self):
        if not os.path.isfile(self.data_file):
            self._logger.info("Updating data structure to: %s" %
                              self.data_file)
            apps = self.parent._settings.get(["registeredApps"])
            self.set_apps(apps)
            self._logger.info("Saved data to: %s" % self.data_file)
            self.parent._settings.remove(["registeredApps"])


    def get_apps(self):
        if os.path.isfile(self.data_file):
            with open(self.data_file, 'r') as file:
                apps = json.load(file)
            if apps is None:
                apps = []
            return apps
        else:
            return []


    def set_apps(self, apps):
        with open(self.data_file, 'w') as outfile:
            json.dump(apps, outfile)
            self.send_settings_plugin_message(apps)


    def send_settings_plugin_message(self, apps):
        mapped_apps = list(map(lambda x: dict(
            displayName=x["displayName"], lastSeenAt=x["lastSeenAt"]), apps))
        self.parent._plugin_manager.send_plugin_message(
            "%s.settings" % self.parent._identifier, {"apps": mapped_apps})


    #
    # SETTINGS
    #


    def get_or_create_encryption_key(self):
        key = self.parent._settings.get(["encryptionKey"])
        if key is None:
            key = str(uuid.uuid4())
            self._logger.info("Created new encryption key")
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