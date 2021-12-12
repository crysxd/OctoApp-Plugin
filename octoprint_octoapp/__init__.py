# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import base64
import hashlib
import json
import logging
import threading
import time
import uuid

import flask
import octoprint.plugin
import requests
from Crypto import Random
from Crypto.Cipher import AES
from octoprint.events import Events


class OctoAppPlugin(
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.ProgressPlugin,
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.SimpleApiPlugin,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.EventHandlerPlugin,
    octoprint.plugin.RestartNeedingPlugin,
):
    def __init__(self):
        self._logger = logging.getLogger("octoprint.plugins.octoapp")

        self.default_config = dict(
            updatePercentModulus=10,
            highPrecisionRangeStart=5,
            highPrecisionRangeEnd=5,
            sendNotificationUrl="https://europe-west1-octoapp-4e438.cloudfunctions.net/sendNotification",
        )
        self.cached_config = self.default_config
        self.cached_config_at = 0

        self.last_progress_notification_at = 0
        self.last_progress = None
        self.last_print_name = None
        self.firmware_info = {}
        self.last_m117_message = None

    def get_settings_defaults(self):
        return dict(registeredApps=[], encryptionKey=None)

    def on_after_startup(self):
        self._logger.info("OctoApp started, updating config")
        self.get_config()
        self.get_or_create_encryption_key()

    def get_template_configs(self):
        return [dict(type="settings", custom_bindings=False)]

    def get_api_commands(self):
        return dict(
            registerForNotifications=[],
            getPrinterFirmware=[],
        )

    def on_print_progress(self, storage, path, progress):
        self.last_progress = progress
        self.last_time_left = self._printer.get_current_data()["progress"][
            "printTimeLeft"
        ]

        # send update, but don't send for 100%
        # we send updated in "modulus" interval as well as for the first and last "modulus" percent
        config = self.get_config()
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
                False,
            )

    def on_event(self, event, payload):
        self._logger.debug("Recevied event %s" % event)
        if event == "PrintStarted":
            self.last_print_name = payload["name"]
            self.last_progress_notification_at = 0
            self.last_m117_message = None
            self.send_m117_plugin_message()

        if event == "PrintDone":
            self.last_progress = None
            self.last_print_name = None
            self.last_time_left = None
            self.send_notification(dict(type="completed", fileName=payload["name"]), True)
            self.last_m117_message = None
            self.send_m117_plugin_message()

        elif event == "PrintFailed" or event == "PrintCancelled":
            self.last_progress = None
            self.last_print_name = None
            self.send_notification(dict(type="idle", fileName=payload["name"]), False)
            self.last_m117_message = None
            self.send_m117_plugin_message()
            
        elif event == "FilamentChange" and self.last_progress is not None:
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
            self.send_m117_plugin_message()

        elif event == "PrintPaused":
            self.send_notification(
                dict(
                    type="paused",
                    fileName=payload["name"],
                    progress=self.last_progress,
                    timeLeft=self.last_time_left,
                ),
                False,
            )

    def send_notification(self, data, highPriority):
        t = threading.Thread(target=self.do_send_notification, args=[data, highPriority])
        t.daemon = True
        t.start()

    def do_send_notification(self, data, highPriority):
        try:
            config = self.get_config()

            # encrypt message and build request body
            data["serverTime"] = int(time.time())
            cipher = AESCipher(self.get_or_create_encryption_key())
            data = cipher.encrypt(json.dumps(data))
            apps = self._settings.get(["registeredApps"])
            if not apps:
                self._logger.debug("No apps registered, skipping notification")
                return

            body = dict(targets=apps, highPriority=highPriority, data=data)
            self._logger.debug("Sending notification %s" % body)

            # make request and check 200
            r = requests.post(config["sendNotificationUrl"], timeout=float(15), json=body)
            if r.status_code != requests.codes.ok:
                raise Exception("Unexpected response code %d" % r.status_code)

            # delete invalid tokens
            apps = self._settings.get(["registeredApps"])
            self._logger.debug("Before updating apps %s" % apps)
            for fcmToken in r.json()["invalidTokens"]:
                apps = [app for app in apps if app["fcmToken"] != fcmToken]
            self._settings.set(["registeredApps"], apps)
            self._settings.save()
            self._logger.debug("Updated apps %s" % apps)
        except Exception as e:
            self._logger.debug("Failed to send notification %s" % e)

    def on_firmware_info_received(
        self, comm_instance, firmware_name, firmware_data, *args, **kwargs
    ):
        self._logger.debug("Recevied firmware info")
        self.firmware_info = firmware_data

    def processGcode(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        if gcode == "M117":
            self.last_m117_message=cmd.split(' ', 1)[1]
            self._logger.debug("M117 message changed: %s" % self.last_m117_message)
            self.send_m117_plugin_message()

    def send_m117_plugin_message(self):
        self._plugin_manager.send_plugin_message(self._identifier, { "m117": self.last_m117_message })

    def on_api_command(self, command, data):
        self._logger.debug("Recevied command %s" % command)

        if command == "getPrinterFirmware":
            return flask.jsonify(self.firmware_info)

        elif command == "registerForNotifications":
            fcmToken = data["fcmToken"]

            # load apps and filter the given FCM token out
            apps = self._settings.get(["registeredApps"])
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
            self._logger.debug("registeredApps %s" % apps)
            self._settings.set(["registeredApps"], apps)
            self._settings.save()

        return flask.jsonify(dict())

    def get_config(self):
        t = threading.Thread(target=self.do_update_config)
        t.daemon = True
        t.start()
        return self.cached_config

    def do_update_config(self):
        # If we have no config cached or the cache is older than a day, request new config
        cache_config_max_age = time.time() - 86400
        if (self.cached_config is not None) and (
            self.cached_config_at > cache_config_max_age
        ):
            return self.cached_config

        # Request config, fall back to default
        try:
            r = requests.get(
                "https://www.octoapp.eu/pluginconfig.json", timeout=float(15)
            )
            if r.status_code != requests.codes.ok:
                raise Exception("Unexpected response code %d" % r.status_code)
            self.cached_config = r.json()
            self.cached_config_at = time.time()
            self._logger.info("OctoApp loaded config: %s" % self.cached_config)
        except Exception as e:
            self._logger.warn(
                "Failed to fetch config using defaults for 5 minutes, recevied %s" % e
            )
            self.cached_config = self.default_config
            self._logger.info("OctoApp loaded config: %s" % self.cached_config)
            self.cached_config_at = cache_config_max_age + 300

    def get_update_information(self):
        return dict(
            octoapp=dict(
                displayName="OctoApp",
                displayVersion=self._plugin_version,
                type="github_release",
                current=self._plugin_version,
                user="crysxd",
                repo="OctoApp-Plugin",
                pip="https://github.com/crysxd/OctoApp-Plugin/archive/{target}.zip",
            )
        )

    def get_or_create_encryption_key(self):
        key = self._settings.get(["encryptionKey"])
        if key is None:
            key = str(uuid.uuid4())
            self._logger.info("Created new encryption key")
            self._settings.set(["encryptionKey"], key)
        return key


__plugin_pythoncompat__ = ">=2.7,<4"
__plugin_implementation__ = OctoAppPlugin()

__plugin_hooks__ = {
    "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
    "octoprint.comm.protocol.firmware.info": __plugin_implementation__.on_firmware_info_received,
    "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.processGcode,
}


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
