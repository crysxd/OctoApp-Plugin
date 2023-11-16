# coding=utf-8
from __future__ import absolute_import
import threading
import socket
from datetime import datetime

import flask
import requests
import octoprint.plugin

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


from .printerstateobject import PrinterStateObject
from .octoprintcommandhandler import OctoPrintCommandHandler
from .octoprintwebcamhelper import OctoPrintWebcamHelper

class OctoeverywherePlugin(octoprint.plugin.StartupPlugin,
                            octoprint.plugin.SettingsPlugin,
                            octoprint.plugin.AssetPlugin,
                            octoprint.plugin.TemplatePlugin,
                            octoprint.plugin.SimpleApiPlugin,
                            octoprint.plugin.EventHandlerPlugin,
                            octoprint.plugin.ProgressPlugin):

    def __init__(self):
        # Default the handler to None since that will make the var name exist
        # but we can't actually create the class yet until the system is more initialized.
        self.NotificationHandler = None
        # Indicates if OnStartup has been called yet.
        self.HasOnStartupBeenCalledYet = False
        # Let the compat system know this is an OctoPrint host.
        Compat.SetIsOctoPrint(True)
    
    # Called when the system is starting up.
    def on_startup(self, host, port):
        # Setup Sentry to capture issues.
        Sentry.Init(self._logger, self._plugin_version, False)

        # Setup our telemetry class.
        Telemetry.Init(self._logger)

        #
        # Due to settings bugs in OctoPrint, as much of the generated values saved into settings should be set here as possible.
        # For more details, see SaveToSettingsIfUpdated()
        #

        # Ensure the plugin version is updated in the settings for the frontend.
        self.EnsurePluginVersionSet()

        # Init the static snapshot helper
        WebcamHelper.Init(self._logger, OctoPrintWebcamHelper(self._logger, self._settings))

        # Setup our printer state object, that implements the interface.
        printerStateObject = PrinterStateObject(self._logger, self._printer)

        # Create the notification object now that we have the logger.
        self.NotificationHandler = NotificationsHandler(self._logger, printerStateObject)
        printerStateObject.SetNotificationHandler(self.NotificationHandler)

        # Create our command handler and our platform specific command handler.
        CommandHandler.Init(self._logger, self.NotificationHandler, OctoPrintCommandHandler(self._logger, self._printer, printerStateObject, self))

        # Indicate this has been called and things have been inited.
        self.HasOnStartupBeenCalledYet = True

    #
    # Functions are for the gcode receive plugin hook
    #
    def received_gcode(self, comm, line, *args, **kwargs):
        # Blocking will block the printer commands from being handled so we can't block here!

        if line and self.NotificationHandler is not None:
            # ToLower the line for better detection.
            lineLower = line.lower()

            # M600 is a filament change command.
            # https://marlinfw.org/docs/gcode/M600.html
            # On my Pursa, I see this "fsensor_update - M600" AND this "echo:Enqueuing to the front: "M600""
            # We check for this both in sent and received, to make sure we cover all use cases. The OnFilamentChange will only allow one notification to fire every so often.
            # This m600 usually comes from when the printer sensor has detected a filament run out.
            if "m600" in lineLower or "fsensor_update" in lineLower:
                self._logger.info("Firing On Filament Change Notification From GcodeReceived: "+str(line))
                # No need to use a thread since all events are handled on a new thread.
                self.NotificationHandler.OnFilamentChange()
            else:
                # Look for a line indicating user interaction is needed.
                if "paused for user" in lineLower or "// action:paused" in lineLower:
                    self._logger.info("Firing On User Interaction Required From GcodeReceived: "+str(line))
                    # No need to use a thread since all events are handled on a new thread.
                    self.NotificationHandler.OnUserInteractionNeeded()

        # We must return line the line won't make it to OctoPrint!
        return line

    def sent_gcode(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        # Blocking will block the printer commands from being handled so we can't block here!

        # M600 is a filament change command.
        # https://marlinfw.org/docs/gcode/M600.html
        # We check for this both in sent and received, to make sure we cover all use cases. The OnFilamentChange will only allow one notification to fire every so often.
        # This M600 usually comes from filament change required commands embedded in the gcode, for color changes and such.
        if self.NotificationHandler is not None and gcode and gcode == "M600":
            self._logger.info("Firing On Filament Change Notification From GcodeSent: "+str(gcode))
            # No need to use a thread since all events are handled on a new thread.
            self.NotificationHandler.OnFilamentChange()

        # Look for positive extrude commands, so we can keep track of them for final snap and our first layer tracking logic.
        # Example cmd value: `G1 X112.979 Y93.81 E.03895`
        if self.NotificationHandler is not None and gcode and cmd and gcode == "G1":
            try:
                indexOfE = cmd.find('E')
                if indexOfE != -1:
                    endOfEValue = cmd.find(' ', indexOfE)
                    if endOfEValue == -1:
                        endOfEValue = len(cmd)
                    eValue = cmd[indexOfE+1:endOfEValue]
                    # The value will look like one of these: -.333,1.33,.33
                    # We don't care about negative values, so ignore them.
                    if eValue[0] != '-':
                        # If the value doesn't start with a 0, the float parse wil fail.
                        if eValue[0] != '0':
                            eValue = "0" + eValue
                        # Now the value should be something like 1.33 or 0.33
                        if float(eValue) > 0:
                            self.NotificationHandler.ReportPositiveExtrudeCommandSent()
            except Exception as e:
                self._logger.debug("Failed to parse gcode %s, error %s", cmd, str(e))

    #
    # Functions are for the Process Plugin
    #
    # pylint: disable=arguments-renamed
    def on_print_progress(self, storage, path, progressInt):
        if self.NotificationHandler is not None:
            self.NotificationHandler.OnPrintProgress(progressInt, None)


    # A dict helper
    def _exists(self, dictObj:dict, key:str) -> bool:
        return key in dictObj and dictObj[key] is not None


    #
    # Functions for the Event Handler Mixin
    #
    # Note that on_event can actually fire before on_startup in some cases.
    #
    def on_event(self, event, payload):
        # This can be called before on_startup where things are inited.
        # Never handle anything that's sent before then.
        if self.HasOnStartupBeenCalledYet is False:
            return

        # Ensure there's a payload
        if payload is None:
            payload = {}

        # Listen for client authed events, these fire whenever a websocket opens and is auth is done.
        if event == "ClientAuthed":
            self.HandleClientAuthedEvent()

        # Only check the event after the notification handler has been created.
        # Specifically here, we have seen the Error event be fired before `on_startup` is fired,
        # and thus the handler isn't created.
        if self.NotificationHandler is None:
            return

        # Listen for the rest of these events for notifications.
        # OctoPrint Events
        if event == "PrintStarted":
            fileName = self.GetDictStringOrEmpty(payload, "name")
            # Gather some stats from other places, if they exist.
            currentData = self._printer.get_current_data()
            fileSizeKBytes = 0
            if self._exists(currentData, "job") and self._exists(currentData["job"], "file") and self._exists(currentData["job"]["file"], "size"):
                fileSizeKBytes = int(currentData["job"]["file"]["size"]) / 1024
            totalFilamentUsageMm = 0
            if self._exists(currentData, "job") and self._exists(currentData["job"], "filament") and self._exists(currentData["job"]["filament"], "tool0") and self._exists(currentData["job"]["filament"]["tool0"], "length"):
                totalFilamentUsageMm = int(currentData["job"]["filament"]["tool0"]["length"])
            self.NotificationHandler.OnStarted(fileName, fileSizeKBytes, totalFilamentUsageMm)
        elif event == "PrintFailed":
            fileName = self.GetDictStringOrEmpty(payload, "name")
            durationSec = self.GetDictStringOrEmpty(payload, "time")
            reason = self.GetDictStringOrEmpty(payload, "reason")
            self.NotificationHandler.OnFailed(fileName, durationSec, reason)
        elif event == "PrintDone":
            fileName = self.GetDictStringOrEmpty(payload, "name")
            durationSec = self.GetDictStringOrEmpty(payload, "time")
            self.NotificationHandler.OnDone(fileName, durationSec)
        elif event == "PrintPaused":
            fileName = self.GetDictStringOrEmpty(payload, "name")
            self.NotificationHandler.OnPaused(fileName)
        elif event == "PrintResumed":
            fileName = self.GetDictStringOrEmpty(payload, "name")
            self.NotificationHandler.OnResume(fileName)

        # Printer Connection
        elif event == "Error":
            error = self.GetDictStringOrEmpty(payload, "error")
            self.NotificationHandler.OnError(error)

        # GCODE Events
        # Note most of these aren't sent when printing from the SD card
        elif event == "Waiting":
            self.NotificationHandler.OnWaiting()
        elif event == "FilamentChange":
            # We also handle some of these filament change gcode events ourselves, but since we already have
            # anti duplication logic in the notification handler for this event, might as well send it here as well.
            self.NotificationHandler.OnFilamentChange()


    def GetDictStringOrEmpty(self, d, key):
        if d[key] is None:
            return ""
        return str(d[key])

    # Ensures the plugin version is set into the settings for the frontend.
    def EnsurePluginVersionSet(self):
        # We save the current plugin version into the settings so the frontend JS can get it.
        self.SaveToSettingsIfUpdated("PluginVersion", self._plugin_version)

    # Returns the frontend http port OctoPrint's http proxy is running on.
    def GetFrontendHttpPort(self):
        # Always try to get and parse the settings value. If the value doesn't exist
        # or it's invalid this will fall back to the default value.
        try:
            return int(self.GetFromSettings("HttpFrontendPort", 80))
        except Exception:
            return 80

    # Returns the if the frontend http proxy for OctoPrint is using https.
    def GetFrontendIsHttps(self):
        # Always try to get and parse the settings value. If the value doesn't exist
        # or it's invalid this will fall back to the default value.
        try:
            return self.GetFromSettings("HttpFrontendIsHttps", False)
        except Exception:
            return False

    # Gets the current setting or the default value.
    def GetBoolFromSettings(self, name, default):
        value = self._settings.get([name])
        if value is None:
            return default
        return value is True

    # Gets the current setting or the default value.
    def GetFromSettings(self, name, default):
        value = self._settings.get([name])
        if value is None:
            return default
        return value

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
    global __plugin_implementation__
    __plugin_implementation__ = OctoeverywherePlugin()

    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.accesscontrol.keyvalidator": __plugin_implementation__.key_validator,
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
        "octoprint.comm.protocol.gcode.received": __plugin_implementation__.received_gcode,
        "octoprint.comm.protocol.gcode.sent": __plugin_implementation__.sent_gcode,
        "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.queuing_gcode,
        # We supply a int here to set our order, so we can be one of the first plugins to execute, to prevent issues.
        # The default order value is 1000
        "octoprint.comm.protocol.scripts": (__plugin_implementation__.script_hook, 1337),
    }
