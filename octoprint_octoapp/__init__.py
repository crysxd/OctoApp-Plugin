# coding=utf-8
from __future__ import absolute_import

import logging
import logging.handlers
from typing import Any, Dict, List, Optional

import flask
import octoprint.plugin
import octoprint.printer
from flask_babel import gettext
from octoprint.access.permissions import (ADMIN_GROUP, READONLY_GROUP,
                                          USER_GROUP)
from octoprint.events import Events

from octoapp.appsstorage import AppStorageHelper
from octoapp.compat import Compat
from octoapp.notificationshandler import NotificationsHandler
from octoapp.sentry import Sentry
from octoapp.logging import TaggedLoggingAdapter
from octoapp.printinfo import PrintInfoManager
from octoapp.notificationutils import NotificationUtils

from .layerprocessor import LayerProcessor
from .mmu2filamentselect import OctoAppMmu2FilamentSelectSubPlugin
from .notifications import OctoAppNotificationsSubPlugin
from .octoprintappstorage import OctoPrintAppStorageSubPlugin
from .printerfirmware import OctoAppPrinterFirmwareSubPlugin
from .printermessage import OctoAppPrinterMessageSubPlugin
from .printerstateobject import PrinterStateObject
from .subplugin import IOctoAppSubPluginParent, OctoAppSubPlugin
from .webcamsnapshots import OctoAppWebcamSnapshotsSubPlugin


class OctoAppPlugin(IOctoAppSubPluginParent):

    def __init__(self):
        # Update logger
        self._logger_handler = None
        self._logger = logging.getLogger("octoprint.plugins.octoapp")
        self.CurrentPluginState:Dict[str,Any] = {}
        self.SubPlugins = []
        self.LastSentPluginState = self.CurrentPluginState
        # Default the handler to None since that will make the var name exist
        # but we can't actually create the class yet until the system is more initialized.
        self.NotificationHandler = None
        # Indicates if OnStartup has been called yet.
        self.HasOnStartupBeenCalledYet = False
        # Let the compat system know this is an OctoPrint host.
        Compat.SetIsOctoPrint(True)

    @property
    def PluginState(self):
        return self.CurrentPluginState

    #
    # Mixins
    #

     # Mixin method
    def on_startup(self, host:str, port:int):
        # Setup Sentry to capture issues.
        self._logger_handler = None
        self._initLogger()
        Sentry.SetLogger(self._logger)
        self.logger.info(f"OctoApp starting {self._plugin_version}")

        # Setup our printer state object, that implements the interface.
        octoPrintPrinterObj:octoprint.printer.PrinterInterface = self._printer #pyright: ignore[reportAssignmentType]
        printerStateObject = PrinterStateObject(self._logger, octoPrintPrinterObj)

        # Setup App storage
        octoPrintAppStorage = OctoPrintAppStorageSubPlugin(logger=TaggedLoggingAdapter(self._logger, "STORAGE"), parent=self)
        AppStorageHelper.Init(logger=TaggedLoggingAdapter(self._logger, "APPS"), appStoragePlatformHelper=octoPrintAppStorage)

        # Create the notification object now that we have the logger.
        self.NotificationHandler = NotificationsHandler(TaggedLoggingAdapter(self._logger, "NOTIFICATIONS"), printerStateObject)
        printerStateObject.SetNotificationHandler(self.NotificationHandler)

        self.SubPlugins:List[OctoAppSubPlugin] = [
            octoPrintAppStorage,
            OctoAppNotificationsSubPlugin(logger=TaggedLoggingAdapter(self._logger, "NOTIFICATIONS/PLUGIN"), parent=self, notificationHandler=self.NotificationHandler),
            OctoAppPrinterMessageSubPlugin(logger=TaggedLoggingAdapter(self._logger, "PRINTER_MESSAGE"), parent=self),
            OctoAppPrinterFirmwareSubPlugin(logger=TaggedLoggingAdapter(self._logger, "PRINTER_FIRMWARE"), parent=self),
            OctoAppMmu2FilamentSelectSubPlugin(logger=TaggedLoggingAdapter(self._logger, "MMU"), parent=self, notificationHandler=self.NotificationHandler),
            OctoAppWebcamSnapshotsSubPlugin(logger=TaggedLoggingAdapter(self._logger, "WEBCAM"), parent=self)
        ]

        # Indicate this has been called and things have been inited.
        self.HasOnStartupBeenCalledYet = True

        # Init print info and utils
        NotificationUtils.Init(TaggedLoggingAdapter(self._logger, "NOTIFICATIONS/UTILS"))
        PrintInfoManager.Init(TaggedLoggingAdapter(self._logger, "PRINT"), self.get_plugin_data_folder())

        # Hook up events
        self._printer.register_callback(self)  # type: ignore

        # Init print info and utils
        NotificationUtils.Init(TaggedLoggingAdapter(self._logger, "NOTIFICATIONS/UTILS"))
        PrintInfoManager.Init(TaggedLoggingAdapter(self._logger, "PRINT"), self.get_plugin_data_folder())


    # Mixin method
    def on_after_startup(self):
        self.logger.info(f"OctoApp started, version is {self._plugin_version}")
        self._settings.set(["version"], self._plugin_version)  # type: ignore

        for sp in self.SubPlugins:
            try:
                sp.OnAfterStartup()
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle after startup", e)


    # Mixin method
    def on_api_command(self, command:str, data:Dict[str,Any]) -> flask.Response: # type: ignore
        self.logger.info(f"Recevied command {command}")

        for sp in self.SubPlugins:
            try:
                res = sp.OnApiCommand(command=command, data=data)
                if res is not None:
                    return res
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle api request", e)
                return flask.make_response("Internal error", 500)

        return flask.make_response("Unkonwn command", 400)


    # Mixin method
    def get_settings_defaults(self):
        return dict(encryptionKey=None, version=self._plugin_version)


    # Mixin method
    def get_template_configs(self):
        return [dict(type="settings", custom_bindings=True)]


    # Mixin method
    def get_api_commands(self) -> Any:
        return dict(
            registerForNotifications=[],
            getPrinterFirmware=[],
            getWebcamSnapshot=[]
        )


    # Mixin method
    def get_assets(self):
        return dict(
            js=[
                "js/octoapp.js"
            ]
        )


    # Mixin method
    def on_print_progress(self, storage:str, path:str, progress:int):
        for sp in self.SubPlugins:
            try:
                sp.OnPrintProgress(storage=storage, path=path, progress=progress)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle progress", e)


    def on_printer_send_current_data(self, data:Dict[str,Any]):
        for sp in self.SubPlugins:
            try:
                sp.OnCurrentData(data=data)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle current data", e)


    # Mixin method
    def on_event(self, event:str, payload:Dict[str,Any]):
        for sp in self.SubPlugins:
            try:
                sp.OnEvent(event=event, payload=payload)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle event", e)

        if event == Events.CLIENT_OPENED:
            self.SendPluginStateMessage(forced=True)


    #
    # EVENTS
    #

    def OnFirmwareInfoReceived(self, comm_instance:Any, firmware_name:str, firmware_data:Dict[str,Any], *args:Any, **kwargs:Any):
        for sp in self.SubPlugins:
            try:
                sp.OnFirmwareInfoReceived(comm_instance, firmware_name, firmware_data, args, kwargs)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle firmware info", e)


    def OnEmitWebsocketMessage(self, user:str, message:str, messageType:str, data:Dict[str,Any]):
        for sp in self.SubPlugins:
            try:
                sp.OnEmitWebsocketMessage(user=user, message=message, messageType=messageType, data=data)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle websocket message", e)

        # Always return true! Returning false will prevent the message from being send
        return True


    def OnGcodeQueued(self, comm_instance:Any, phase:Any, cmd:str, cmd_type:str, gcode:str, *args:Any, **kwargs:Any):
        send = True
        for sp in self.SubPlugins:
            try:
                send = send and sp.OnGcodeQueued(comm_instance=comm_instance, phase=phase, cmd=cmd, cmd_type=cmd_type, gcode=gcode, args=args, kwargs=kwargs)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle gcode queued", e)

        # If we should not send the ocmmand to the printer, return a None, value
        if send is False:
            self.logger.debug("Supressing Gcode: " + cmd)
            return None, # pylint: disable=trailing-comma-tuple

        return None


    def OnGcodeSent(self, comm_instance:Any, phase:Any, cmd:str, cmd_type:str, gcode:str, *args:Any, **kwargs:Any):
        for sp in self.SubPlugins:
            try:
                sp.OnGcodeSent(comm_instance=comm_instance, phase=phase, cmd=cmd, cmd_type=cmd_type, gcode=gcode, args=args, kwargs=kwargs)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle gcode sent", e)


    def OnGcodeReceived(self, comm_instance:Any, line:str, *args:Any, **kwargs:Any):
        for sp in self.SubPlugins:
            try:
                sp.OnGcodeReceived(comm_instance=comm_instance, line = line, args=args, kwargs=kwargs)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to handle gcode received", e)

        # We must return line the line won't make it to OctoPrint!
        return line


    def SendPluginStateMessage(self, forced=False):
        # Only send if we are forced to update or the state actually changed
        if forced or self.LastSentPluginState != self.CurrentPluginState:
            self.LastSentPluginState = self.CurrentPluginState.copy()
            self._plugin_manager.send_plugin_message(self._identifier, self.CurrentPluginState)  # type: ignore


    #
    # Utility methods
    #

    def GetUpdateInformation(self):
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


    def GetAdditionalPermissions(self, *args:Any, **kwargs:Any) -> Any:
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


    def _initLogger(self):
        self._logger_handler = logging.handlers.RotatingFileHandler(
            self._settings.get_plugin_logfile_path(),   # type: ignore
            maxBytes=512 * 1024,
            backupCount=1
        )
        self._logger_handler.setFormatter(logging.Formatter("%(levelname)-8s | %(asctime)s | %(message)s"))
        self._logger_handler.setLevel(logging.DEBUG)
        self._logger.addHandler(self._logger_handler)
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self.logger = TaggedLoggingAdapter(self._logger, "MAIN")


__plugin_name__ = "OctoApp"
__plugin_pythoncompat__ = ">=3.0,<4" # Only PY3


# pylint: disable=global-statement
def __plugin_load__():
    global __plugin_pythoncompat__
    __plugin_pythoncompat__ = ">=3,<4"

    global __plugin_implementation__
    __plugin_implementation__ = OctoAppPlugin()


    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.GetUpdateInformation,
        "octoprint.comm.protocol.gcode.received": __plugin_implementation__.OnGcodeReceived,
        "octoprint.comm.protocol.gcode.sent": __plugin_implementation__.OnGcodeSent,
        "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.OnGcodeQueued,
        "octoprint.comm.protocol.firmware.info": __plugin_implementation__.OnFirmwareInfoReceived,
        "octoprint.access.permissions": __plugin_implementation__.GetAdditionalPermissions,
        "octoprint.server.sockjs.emit": __plugin_implementation__.OnEmitWebsocketMessage,
        "octoprint.filemanager.preprocessor": LayerProcessor.InsertLayerChanges
    }
