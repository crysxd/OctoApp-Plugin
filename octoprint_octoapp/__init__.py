# coding=utf-8
from __future__ import absolute_import
import threading
import socket
from datetime import datetime

import time
import flask
from flask_babel import gettext
import requests
import octoprint.plugin
import logging
import logging.handlers
from octoprint.access.permissions import ADMIN_GROUP, USER_GROUP, READONLY_GROUP
from octoprint.events import Events

from octoapp.webcamhelper import WebcamHelper
from octoapp.octoeverywhereimpl import OctoEverywhere
from octoapp.octohttprequest import OctoHttpRequest
from octoapp.notificationshandler import NotificationsHandler
from octoapp.octopingpong import OctoPingPong
from octoapp.telemetry import Telemetry
from octoapp.sentry import Sentry
from octoapp.mdns import MDns
from octoapp.hostcommon import HostCommon
from octoapp.Proto.ServerHost import ServerHost
from octoapp.commandhandler import CommandHandler
from octoapp.compat import Compat

from .notifications import OctoAppNotificationsSubPlugin
from .printermessage import OctoAppPrinterMessageSubPlugin
from .printerfirmware import OctoAppPrinterFirmwareSubPlugin
from .mmu2filamentselect import OctoAppMmu2FilamentSelectSubPlugin
from .webcamsnapshots import OctoAppWebcamSnapshotsSubPlugin

from .printerstateobject import PrinterStateObject
from .octoprintcommandhandler import OctoPrintCommandHandler
from .octoprintwebcamhelper import OctoPrintWebcamHelper

class OctoAppPlugin(octoprint.plugin.AssetPlugin,
                    octoprint.plugin.ProgressPlugin,
                    octoprint.plugin.StartupPlugin,
                    octoprint.plugin.TemplatePlugin,
                    octoprint.plugin.SimpleApiPlugin,
                    octoprint.plugin.SettingsPlugin,
                    octoprint.plugin.EventHandlerPlugin,
                    octoprint.plugin.RestartNeedingPlugin):
    
    def __init__(self):
        # Update logger
        self._logger = logging.getLogger("octoprint.plugins.octoapp")
        # Create default config
        self.DefaultConfig = dict(
            updatePercentModulus=5,
            highPrecisionRangeStart=5,
            highPrecisionRangeEnd=5,
            minIntervalSecs=300,
            sendNotificationUrl="https://europe-west1-octoapp-4e438.cloudfunctions.net/sendNotificationV2",
        )
        self.CachedConfig = self.DefaultConfig
        self.CachedConfig_at = 0
        self.PluginState = {}
        self.LastSentPluginState = {}
        # Default the handler to None since that will make the var name exist
        # but we can't actually create the class yet until the system is more initialized.
        self.NotificationHandler = None
        # Indicates if OnStartup has been called yet.
        self.HasOnStartupBeenCalledYet = False
        # Let the compat system know this is an OctoPrint host.
        Compat.SetIsOctoPrint(True)

    #
    # EVENTS
    #

    # Called when the system is starting up.
    def on_startup(self, host, port):
        # Setup Sentry to capture issues.
        self._init_logger()
        Sentry.Init(self._logger, self._plugin_version, False)
        Sentry.Info("PLUGIN", "OctoApp starting" % self._plugin_version)

        #
        # Due to settings bugs in OctoPrint, as much of the generated values saved into settings should be set here as possible.
        # For more details, see SaveToSettingsIfUpdated()
        #

        # Init the static snapshot helper
        WebcamHelper.Init(self._logger, OctoPrintWebcamHelper(self._logger, self._settings))

        # Setup our printer state object, that implements the interface.
        printerStateObject = PrinterStateObject(self._logger, self._printer)

        # Create the notification object now that we have the logger.
        self.NotificationHandler = NotificationsHandler(self._logger, printerStateObject)
        printerStateObject.SetNotificationHandler(self.NotificationHandler)

        # Create our command handler and our platform specific command handler.
        CommandHandler.Init(self._logger, self.NotificationHandler, OctoPrintCommandHandler(self._logger, self._printer, printerStateObject, self))

        self.sub_plugins = [
            OctoAppNotificationsSubPlugin(NotificationsHandler),
            OctoAppPrinterMessageSubPlugin(self),
            OctoAppPrinterFirmwareSubPlugin(self),
            OctoAppMmu2FilamentSelectSubPlugin(self, NotificationsHandler),
            OctoAppWebcamSnapshotsSubPlugin(self)
        ]

        for sp in self.sub_plugins:
            sp.config = self.DefaultConfig

        # Indicate this has been called and things have been inited.
        self.HasOnStartupBeenCalledYet = True

    def on_after_startup(self):
        self._logger_handler()
        Sentry.Info("PLUGIN", "OctoApp started, updating config, version is %s" % self._plugin_version)
        self.update_config()
        self._settings.set(["version"], self._plugin_version)

        for sp in self.sub_plugins:
            try:
                sp.on_after_startup()
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle after startup", e)

    def _init_logger(self):
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


    def on_firmware_info_received(self, comm_instance, firmware_name, firmware_data, *args, **kwargs):
        for sp in self.sub_plugins:
            try:
                sp.on_firmware_info_received(comm_instance, firmware_name, firmware_data, args, kwargs)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle firmware info", e)

    def on_api_command(self, command, data):
        Sentry.Info("PLUGIN", "Recevied command %s" % command)

        for sp in self.sub_plugins:
            try:
                res = sp.on_api_command(command=command, data=data)
                if res != None:
                    return res
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle api request", e)
                return flask.make_response("Internal error", 500)

        return flask.make_response("Unkonwn command", 400)


    def on_emit_websocket_message(self, user, message, type, data):
        for sp in self.sub_plugins:
            try:
                sp.on_emit_websocket_message(user=user, message=message, type=type, data=data)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle websocket message", e)

        # Always return true! Returning false will prevent the message from being send
        return True


    def on_print_progress(self, storage, path, progress):
        for sp in self.sub_plugins:
            try:
                sp.on_print_progress(storage=storage, path=path, progress=progress)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle progress", e)

    def on_event(self, event, payload):
        for sp in self.sub_plugins:
            try:
                sp.on_event(event=event, payload=payload)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle event", e)
        
        if event == Events.CLIENT_OPENED:
            self.send_plugin_state_message(forced=True)


    def on_gcode_queued(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        for sp in self.sub_plugins:
            try:
                sp.on_gcode_queued(comm_instance=comm_instance, phase=phase, cmd=cmd, cmd_type=cmd_type, gcode=gcode, args=args, kwargs=kwargs)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle gcode queued", e)

    def on_gcode_sent(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        for sp in self.sub_plugins:
            try:
                sp.on_gcode_sent(comm_instance=comm_instance, phase=phase, cmd=cmd, cmd_type=cmd_type, gcode=gcode, args=args, kwargs=kwargs)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle gcode sent", e)


    def on_gcode_received(self, comm_instance, line, *args, **kwargs):
        for sp in self.sub_plugins:
            try:
                sp.on_gcode_received(comm_instance=comm_instance, line = line, args=args, kwargs=kwargs)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle gcode received", e)

    def send_plugin_state_message(self, forced=False):
        # Only send if we are forced to update or the state actually changed
        if forced or self.LastSentPluginState != self.PluginState:
            self.LastSentPluginState = self.PluginState.copy()
            self._plugin_manager.send_plugin_message(
                self._identifier, self.PluginState)

    #
    # CONFIG
    #

    def update_config(self):
        t = threading.Thread(target=self.do_update_config)
        t.daemon = True
        t.start()
        return self.CachedConfig

    def do_update_config(self):
        # If we have no config cached or the cache is older than a day, request new config
        cache_config_max_age = time.time() - 86400
        if (self.CachedConfig is not None) and (
            self.CachedConfig_at > cache_config_max_age
        ):
            return self.CachedConfig

        # Request config, fall back to default
        try:
            r = requests.get(
                "https://www.octoapp.eu/config/plugin.json", timeout=float(15)
            )
            if r.status_code != requests.codes.ok:
                raise Exception("Unexpected response code %d" % r.status_code)
            self.CachedConfig = r.json()
            self.CachedConfig_at = time.time()
    
            for sp in self.sub_plugins:
                sp.config = self.DefaultConfig
        except Exception as e:
            Sentry.ExceptionNoSend("Failed to fetch config using defaults for 5 minutes", e)
            self.CachedConfig = self.DefaultConfig
            self.CachedConfig_at = cache_config_max_age + 300
        
        Sentry.Info("PLUGIN", "OctoApp loaded config: %s" % self.CachedConfig)

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


    # Saves the value into to the settings object if the value changed.
    def SaveToSettingsIfUpdated(self, name, value):
        #
        # A quick note about settings and creating / saving settings during startup!
        #
        # Notes about _settings:
        #    - The force=True MUST ALWAYS BE USED for the .set() function. This is because we don't offer any default settings in get_settings_defaults, and if we don't use the force flag
        #      the setting doesn't match an existing path is ignored.
        #    - We should only set() and save() the settings when things actually change to prevent race conditions with anything else in OctoPrint writing to or saving settings.
        #    - Ideally anything that needs to be generated and written into the settings should happen IN SYNC during the on_startup or on_after_startup calls.
        #
        # We had a bug where OctoEverywhere would put OctoPrint into Safe Mode on the next reboot. After hours of debugging
        # we realized it was because when we updated and saved settings. The OctoPrint safe mode can get triggered when the var `incompleteStartup` remains set to True in the OctoPrint config.
        # This flag is set to true on startup and then set to false after `on_after_startup` is called on all plugins. The problem was our logic in on_after_startup raced the clearing logic of
        # that flag and sometimes resulted in it not being unset.
        #
        curValue = self.GetFromSettings(name, None)
        if curValue is None or curValue != value:
            self._logger.info("Value "+str(name)+" has changed so we are updating the value in settings and saving.")
            self._settings.set([name], value, force=True)
            self._settings.save(force=True)

__plugin_name__ = "OctoEverywhere!"
__plugin_pythoncompat__ = ">=3.0,<4" # Only PY3

def __plugin_load__():
    global __plugin_pythoncompat__
    __plugin_pythoncompat__ = ">=3,<4"

    global __plugin_implementation__
    __plugin_implementation__ = OctoAppPlugin()


    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
        "octoprint.comm.protocol.gcode.received": __plugin_implementation__.received_gcode,
        "octoprint.comm.protocol.gcode.sent": __plugin_implementation__.sent_gcode,
        "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.queuing_gcode,
        "octoprint.comm.protocol.firmware.info": __plugin_implementation__.on_firmware_info_received,
        "octoprint.access.permissions": __plugin_implementation__.get_additional_permissions,
        "octoprint.server.sockjs.emit": __plugin_implementation__.on_emit_websocket_message,
    }
