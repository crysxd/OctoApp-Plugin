import logging
import logging.handlers
import threading
import flask
from flask_babel import gettext
import octoprint.plugin
import requests
import time
from octoprint.access.permissions import ADMIN_GROUP, USER_GROUP, READONLY_GROUP
from octoprint.events import Events

from .notifications import OctoAppNotificationsSubPlugin
from .printer_message import OctoAppPrinterMessageSubPlugin
from .printer_firmware import OctoAppPrinterFirmwareSubPlugin
from .mmu2_filament_select import OctoAppMmu2FilamentSelectSubPlugin
from .webcam_snapshots import OctoAppWebcamSnapshotsSubPlugin

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
            updatePercentModulus=5,
            highPrecisionRangeStart=5,
            highPrecisionRangeEnd=5,
            minIntervalSecs=300,
            sendNotificationUrl="https://europe-west1-octoapp-4e438.cloudfunctions.net/sendNotificationV2",
        )

        self.cached_config = self.default_config
        self.cached_config_at = 0
        self.plugin_state = {}
        self.last_send_plugin_state = {}

        # !!! Also update in setup.py !!!!
        self.plugin_version = "1.3.1"

        notification_plugin =  OctoAppNotificationsSubPlugin(self)
        self.sub_plugins = [
            notification_plugin,
            OctoAppPrinterMessageSubPlugin(self),
            OctoAppPrinterFirmwareSubPlugin(self),
            OctoAppMmu2FilamentSelectSubPlugin(self, notification_plugin),
            OctoAppWebcamSnapshotsSubPlugin(self)
        ]

        for sp in self.sub_plugins:
            sp.config = self.default_config

    #
    # EVENTS
    #

    def on_after_startup(self):
        self._logger_handler = logging.handlers.RotatingFileHandler(
            self._settings.get_plugin_logfile_path(), 
            maxBytes=512 * 1024,
            backupCount=1
        )
        self._logger_handler.setFormatter(logging.Formatter("%(levelname)-8s | %(asctime)s | %(message)s"))
        self._logger_handler.setLevel(logging.DEBUG)
        self._logger.addHandler(self._logger_handler)
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
    
        self._logger.info("PLUGIN       | OctoApp started, updating config, version is %s" % self._plugin_version)

        self.update_config()
        self._settings.set(["version"], self.plugin_version)

        for sp in self.sub_plugins:
            try:
                sp.on_after_startup()
            except Exception as e:
                self._logger.warning("PLUGIN       | Failed to handle after startup %s" , e, exc_info=True)


    def on_firmware_info_received(self, comm_instance, firmware_name, firmware_data, *args, **kwargs):
        for sp in self.sub_plugins:
            try:
                sp.on_firmware_info_received(comm_instance, firmware_name, firmware_data, args, kwargs)
            except Exception as e:
                self._logger.warning("PLUGIN       | Failed to handle firmware info %s" , e, exc_info=True)


    def on_api_command(self, command, data):
        self._logger.debug("PLUGIN       |Recevied command %s" % command)

        for sp in self.sub_plugins:
            try:
                res = sp.on_api_command(command=command, data=data)
                if res != None:
                    return res
            except Exception as e:
                self._logger.warning("PLUGIN       | Failed to handle api request %s" , e, exc_info=True)
                return flask.make_response("Internal error", 500)

        return flask.make_response("PLUGIN       | Unkonwn command", 400)


    def on_emit_websocket_message(self, user, message, type, data):
        for sp in self.sub_plugins:
            try:
                sp.on_emit_websocket_message(user=user, message=message, type=type, data=data)
            except Exception as e:
                self._logger.warning("PLUGIN       | Failed to handle websocket message %s" , e, exc_info=True)

        # Always return true! Returning false will prevent the message from being send
        return True


    def on_print_progress(self, storage, path, progress):
        for sp in self.sub_plugins:
            try:
                sp.on_print_progress(storage=storage, path=path, progress=progress)
            except Exception as e:
                self._logger.warning("PLUGIN       | Failed to handle progress %s" , e, exc_info=True)


    def on_event(self, event, payload):
        for sp in self.sub_plugins:
            try:
                sp.on_event(event=event, payload=payload)
            except Exception as e:
                self._logger.warning("PLUGIN       | Failed to handle event %s" , e, exc_info=True)
        
        if event == Events.CLIENT_OPENED:
            self.send_plugin_state_message(forced=True)


    def on_gcode_send(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        for sp in self.sub_plugins:
            try:
                sp.on_gcode_send(comm_instance=comm_instance, phase=phase, cmd=cmd, cmd_type=cmd_type, gcode=gcode, args=args, kwargs=kwargs)
            except Exception as e:
                self._logger.warning("PLUGIN       | Failed to handle gcode %s" , e, exc_info=True)


    def send_plugin_state_message(self, forced=False):
        # Only send if we are forced to update or the state actually changed
        if forced or self.last_send_plugin_state != self.plugin_state:
            self.last_send_plugin_state = self.plugin_state.copy()
            self._plugin_manager.send_plugin_message(
                self._identifier, self.plugin_state)

    #
    # CONFIG
    #

    def update_config(self):
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
                "https://www.octoapp.eu/config/plugin.json", timeout=float(15)
            )
            if r.status_code != requests.codes.ok:
                raise Exception("Unexpected response code %d" % r.status_code)
            self.cached_config = r.json()
            self.cached_config_at = time.time()
            self._logger.info("PLUGIN       | OctoApp loaded config: %s" % self.cached_config)
    
            for sp in self.sub_plugins:
                sp.config = self.default_config
        except Exception as e:
            self._logger.warning(
                "PLUGIN       | Failed to fetch config using defaults for 5 minutes, recevied %s" % e
            )
            self.cached_config = self.default_config
            self._logger.info("PLUGIN       | OctoApp loaded config: %s" % self.cached_config)
            self.cached_config_at = cache_config_max_age + 300

    #
    # MISC
    #

    def get_settings_defaults(self):
        return dict(encryptionKey=None, version=self.plugin_version)

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