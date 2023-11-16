
from .subplugin import OctoAppSubPlugin
from octoapp.notificationshandler import NotificationsHandler
from octoapp.sentry import Sentry

class OctoAppNotificationsSubPlugin(OctoAppSubPlugin):

    def __init__(self, parent, notification_handler: NotificationsHandler):
        super().__init__(parent)
        self.notifications = notification_handler

    def on_print_progress(self, storage, path, progress):
        if self.NotificationHandler is not None:
            self.NotificationHandler.OnPrintProgress(progress, None)

    def on_event(self, event, payload):
          # Only check the event after the notification handler has been created.
        # Specifically here, we have seen the Error event be fired before `on_startup` is fired,
        # and thus the handler isn't created.
        if self.NotificationHandler is None:
            return

        # Ensure there's a payload
        if payload is None:
            payload = {}

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

    def on_gcode_sent(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
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
    
    def on_gcode_received(self, comm_instance, line, *args, **kwargs):
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
    
    # A dict helper
    def _exists(self, dictObj:dict, key:str) -> bool:
        return key in dictObj and dictObj[key] is not None

    def GetDictStringOrEmpty(self, d, key):
        if d[key] is None:
            return ""
        return str(d[key])

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
    