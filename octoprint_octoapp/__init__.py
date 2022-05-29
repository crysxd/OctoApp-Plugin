# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals
from asyncio.log import logger

import base64
import hashlib
import json
import logging
from pkgutil import get_data
import threading
import time
from typing import Dict
import uuid
import os

import flask
from flask import send_file, request
from io import BytesIO
from flask_babel import gettext
import octoprint.plugin
import requests
from PIL import Image
from Crypto import Random
from Crypto.Cipher import AES
from octoprint.events import Events
from octoprint.access.permissions import Permissions, ADMIN_GROUP, USER_GROUP, READONLY_GROUP


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
        self.plugin_state = {}
        self.last_send_plugin_state = {}

    #
    # EVENTS
    #

    def on_after_startup(self):
        self._logger.info("OctoApp started, updating config")
        self.data_file = os.path.join(
            self.get_plugin_data_folder(), "apps.json")
        self.get_config()
        self.get_or_create_encryption_key()
        self.update_data_structure()

    def on_firmware_info_received(
        self, comm_instance, firmware_name, firmware_data, *args, **kwargs
    ):
        self._logger.debug("Recevied firmware info")
        self.firmware_info = firmware_data

    def on_api_command(self, command, data):
        self._logger.debug("Recevied command %s" % command)

        if command == "getPrinterFirmware":
            if not Permissions.PLUGIN_OCTOAPP_GET_DATA.can():
                return flask.make_response("Insufficient rights", 403)
            return flask.jsonify(self.firmware_info)

        elif command == "registerForNotifications":
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
            self._settings.save()
            return flask.jsonify(dict())

        elif command == "getWebcamSnapshot":
            if not Permissions.PLUGIN_OCTOAPP_GET_DATA.can():
                return flask.make_response("Insufficient rights", 403)

            try:
                webcamIndex = data.get("webcamIndex", 0)
                if (webcamIndex == 0):
                    snapshotUrl = self._settings.global_get(
                        ["webcam", "snapshot"]
                    )
                else:
                    snapshotUrl = self._settings.global_get(
                        ["plugins", "multicam", "multicam_profiles"]
                    )[webcamIndex]["snapshot"]
                timeout = self._settings.global_get_int(
                    ["webcam", "snapshotTimeout"]
                )
                self._logger.debug(
                    "Getting snapshot from {0} (index {1})".format(
                        snapshotUrl, webcamIndex)
                )
                response = requests.get(
                    snapshotUrl, timeout=float(timeout), stream=True)
                image = Image.open(response.raw)

                if (self._settings.global_get_boolean(["webcam", "rotate90"])):
                    image = image.rotate(90, expand=True)

                if (self._settings.global_get_boolean(["webcam", "flipV"])):
                    image = image.transpose(Image.FLIP_TOP_BOTTOM)

                if (self._settings.global_get_boolean(["webcam", "flipH"])):
                    image = image.transpose(Image.FLIP_LEFT_RIGHT)

                size = int(data.get("size", 720))
                image.thumbnail([size, size])
                imageBytes = BytesIO()
                try:
                    image.save(imageBytes, 'WEBP',
                               quality=data.get("quality", 70))
                    imageBytes.seek(0)
                    return send_file(imageBytes, mimetype='image/webp')
                except:
                    image.save(imageBytes, 'JPEG',
                               quality=data.get("quality", 50))
                    imageBytes.seek(0)
                    return send_file(imageBytes, mimetype='image/jpeg')
            except Exception as e:
                self._logger.warning("Failed to get webcam snapshot %s" % e)
                return flask.make_response("Failed to get snapshot from webcam", 500)

        return flask.make_response("Unkonwn command", 400)

    def on_emit_websocket_message(self, user, message, type, data):
        try:
            if type == "plugin" and data.get("plugin") == "mmu2filamentselect" and isinstance(data.get("data"), dict):
                action = data.get("data").get("action")

                if action == "show":
                    # If not currently active, send notification as we switched state
                    if self.plugin_state.get("mmuSelectionActive") is not True:
                        self.send_notification(
                            dict(
                                type="mmu_filament_selection_started",
                                fileName=self.last_print_name,
                                progress=self.last_progress,
                                timeLeft=self.last_time_left,
                            ),
                            True,
                        )

                    self.plugin_state["mmuSelectionActive"] = True
                    self.send_plugin_state_message()

                elif action == "close":
                    # If currently active, send notification as we switched state
                    if self.plugin_state.get("mmuSelectionActive") is True:
                        self.send_notification(
                            dict(
                                type="mmu_filament_selection_completed",
                                fileName=self.last_print_name,
                                progress=self.last_progress,
                                timeLeft=self.last_time_left,
                            ),
                            True,
                        )

                    self.plugin_state["mmuSelectionActive"] = False
                    self.send_plugin_state_message()

        except Exception as e:
            self._logger.error(
                "Exception while checking websocket message: %s" % e)

        # Always return true! Returning false will prevent the message from being send
        return True

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
        if event == Events.PRINT_STARTED:
            self.last_print_name = payload["name"]
            self.last_progress_notification_at = 0
            self.plugin_state["m117"] = None
            self.send_plugin_state_message()

        if event == Events.PRINT_DONE:
            self.last_progress = None
            self.last_print_name = None
            self.last_time_left = None
            self.send_notification(
                dict(type="completed", fileName=payload["name"]), True)
            self.plugin_state["m117"] = None
            self.send_plugin_state_message()

        elif event == Events.PRINT_FAILED or event == Events.PRINT_CANCELLED:
            self.last_progress = None
            self.last_print_name = None
            self.send_notification(
                dict(type="idle", fileName=payload["name"]), False)
            self.plugin_state["m117"] = None
            self.send_plugin_state_message()

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
            self.send_plugin_state_message(forced=True)
            self.send_settings_plugin_message(self.get_apps())

        elif event == Events.PRINT_PAUSED:
            self.send_notification(
                dict(
                    type="paused",
                    fileName=payload["name"],
                    progress=self.last_progress,
                    timeLeft=self.last_time_left,
                ),
                False,
            )

    def on_gcode_send(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        if gcode == "M117":
            message = cmd.split(' ', 1)[1]
            self.plugin_state["m117"] = message
            self._logger.debug("M117 message changed: %s" % message)
            self.send_plugin_state_message()

        elif gcode == "M300":
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
                False,
            )
            return

    #
    # NOTIFICATIONS
    #

    def send_notification(self, data, highPriority):
        t = threading.Thread(target=self.do_send_notification, args=[
                             data, highPriority])
        t.daemon = True
        t.start()

    def do_send_notification(self, data, highPriority):
        try:
            config = self.get_config()

            # encrypt message and build request body
            data["serverTime"] = int(time.time())
            data["serverTimePrecise"] = time.time()
            cipher = AESCipher(self.get_or_create_encryption_key())
            data = cipher.encrypt(json.dumps(data))
            apps = self.get_apps()
            if not apps:
                self._logger.debug("No apps registered, skipping notification")
                return

            body = dict(targets=apps, highPriority=highPriority, data=data)
            self._logger.debug("Sending notification %s" % body)

            # make request and check 200
            r = requests.post(config["sendNotificationUrl"],
                              timeout=float(15), json=body)
            if r.status_code != requests.codes.ok:
                raise Exception("Unexpected response code %d" % r.status_code)

            # delete invalid tokens
            apps = self.get_apps()
            self._logger.debug("Before updating apps %s" % apps)
            for fcmToken in r.json()["invalidTokens"]:
                apps = [app for app in apps if app["fcmToken"] != fcmToken]
            self.set_apps(apps)
            self._settings.save()
            self._logger.debug("Updated apps %s" % apps)
        except Exception as e:
            self._logger.debug("Failed to send notification %s" % e)

    def send_plugin_state_message(self, forced=False):
        # Only send if we are forced to update or the state actually changed
        if forced or self.last_send_plugin_state != self.plugin_state:
            self.last_send_plugin_state = self.plugin_state.copy()
            self._plugin_manager.send_plugin_message(
                self._identifier, self.plugin_state)

    #
    # CONFIG
    #

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
            self._logger.warning(
                "Failed to fetch config using defaults for 5 minutes, recevied %s" % e
            )
            self.cached_config = self.default_config
            self._logger.info("OctoApp loaded config: %s" % self.cached_config)
            self.cached_config_at = cache_config_max_age + 300

    #
    # APPS
    #

    def update_data_structure(self):
        if not os.path.isfile(self.data_file):
            self._logger.info("Updating data structure to: %s" %
                              self.data_file)
            apps = self._settings.get(["registeredApps"])
            self.set_apps(apps)
            self._logger.info("Saved data to: %s" % self.data_file)
            self._settings.remove(["registeredApps"])

    def get_apps(self):
        with open(self.data_file, 'r') as file:
            apps = json.load(file)
        if apps is None:
            apps = []
        return apps

    def set_apps(self, apps):
        with open(self.data_file, 'w') as outfile:
            json.dump(apps, outfile)
            self.send_settings_plugin_message(apps)

    def send_settings_plugin_message(self, apps):
        mapped_apps = list(map(lambda x: dict(
            displayName=x["displayName"], lastSeenAt=x["lastSeenAt"]), apps))
        self._plugin_manager.send_plugin_message(
            "%s.settings" % self._identifier, {"apps": mapped_apps})

    #
    # MISC
    #

    def get_settings_defaults(self):
        return dict(encryptionKey=None, version=self._plugin_version)

    def get_template_configs(self):
        return [dict(type="settings", custom_bindings=True)]

    def get_api_commands(self):
        return dict(
            registerForNotifications=[],
            getPrinterFirmware=[],
            getWebcamSnapshot=[]
        )

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

    def get_additional_permissions(self, *args, **kwargs):
        return [
            dict(key="RECEIVE_NOTIFICATIONS",
                 name="Receive push notifications",
                 description=gettext(
                     "Allows to register OctoApp installations to receive notifications"
                 ),
                 roles=["admin"],
                 dangerous=False,
                 default_groups=[ADMIN_GROUP, USER_GROUP, READONLY_GROUP]),
            dict(key="GET_DATA",
                 name="Get additional data",
                 description=gettext(
                     "Allows OctoApp to get additional data"
                 ),
                 roles=["admin"],
                 dangerous=False,
                 default_groups=[ADMIN_GROUP, USER_GROUP, READONLY_GROUP])
        ]

    def get_assets(self):
        return dict(
            js=[
                "js/octoapp.js"
            ]
        )


__plugin_pythoncompat__ = ">=2.7,<4"
__plugin_implementation__ = OctoAppPlugin()

__plugin_hooks__ = {
    "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
    "octoprint.comm.protocol.firmware.info": __plugin_implementation__.on_firmware_info_received,
    "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.on_gcode_send,
    "octoprint.access.permissions": __plugin_implementation__.get_additional_permissions,
    "octoprint.server.sockjs.emit": __plugin_implementation__.on_emit_websocket_message,
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
