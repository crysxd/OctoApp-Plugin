import math
import threading
import time
import secrets
import string
from typing import Any, Dict, List, Optional, Tuple

from .bedcooldownwatcher import BedCooldownWatcher
from .buffer import ByteLikeOrMemoryView
from .compat import Compat
from .interfaces import INotificationHandler, IPrinterStateReporter
from .logging import LoggerLike, TaggedLoggingAdapter
from .notificationsender import NotificationSender
from .printinfo import PrintInfo, PrintInfoManager
from .repeattimer import RepeatTimer
from .sentry import Sentry
from .snapshotresizeparams import SnapshotResizeParams


class ProgressCompletionReportItem:
    def __init__(self, value:float, reported:bool):
        self.value = value
        self.reported = reported

    def Value(self) -> float:
        return self.value

    def Reported(self) -> bool:
        return self.reported

    def SetReported(self, reported:bool):
        self.reported = reported


class NotificationsHandler(INotificationHandler):

    # This is the max snapshot file size we will allow to be sent.
    MaxSnapshotFileSizeBytes = 2 * 1024 * 1024

    # The length of the random print id. This must be a large number, since it needs to be
    # globally unique. This value must stay in sync with the service.
    PrintIdLength = 60


    def __init__(self, logger:LoggerLike, printerStateInterface:IPrinterStateReporter):
        self.Logger = logger
        # On init, set the key to empty.
        self.OctoKey = None
        self.ProtocolAndDomain = None
        self.PrinterStateInterface = printerStateInterface
        self.NotificationSender = NotificationSender(logger=TaggedLoggingAdapter(logger, "SENDER"))
        self.ProgressTimer = None
        self.FirstLayerTimer = None
        self.PauseThread:Optional[StoppableThread] = None
        self.BedCooldownWatcher = BedCooldownWatcher(logger, self, self.PrinterStateInterface)

        # Define all the vars we use locally in the notification handler
        self.PrintCookie = ""
        self.FallbackProgressInt = 0
        self.MoonrakerReportedProgressFloat_CanBeNone:Optional[float] = None
        self.PingTimerHoursReported = 0
        self.HasSendFirstLayerDoneMessage = False
        self.HasSendThirdLayerDoneMessage = False
        self.zOffsetLowestSeenMM = 1337.0
        self.zOffsetNotAtLowestCount = 0
        self.zOffsetHasSeenPositiveExtrude = False
        self.zOffsetTrackingStartTimeSec = 0.0
        self.FirstLayerDoneSince = 0.0
        self.ThirdLayerDoneSince = 0.0
        self.ProgressCompletionReported = []
        self.RestorePrintProgressPercentage = False
        self.CustomNotificationCounter = 0
        self.CustomNotificationLimit = 25

        self.SpammyEventTimeDict:dict[str, SpammyEventContext] = {}
        self.SpammyEventLock = threading.Lock()

        # Call this to init all of the vars to their default values.
        # But we pass none, so we don't delete any print infos that might be on disk we will try to recover when connected to the server.
        self._RecoverOrRestForNewPrint(None)


    # Called to start a new print.
    # On class init, this can be called with printCookie=None, but after that we should always have a print cookie.
    def _RecoverOrRestForNewPrint(self, printCookie:Optional[str]):
        # We always reset these local notification handler values for new prints or recovered prints.
        self.FallbackProgressInt = 0
        self.MoonrakerReportedProgressFloat_CanBeNone = None
        self.PingTimerHoursReported = 0
        self.HasSendFirstLayerDoneMessage = False
        self.HasSendThirdLayerDoneMessage = False
        self.FirstLayerDoneSince = 0.0
        self.ThirdLayerDoneSince = 0.0
        # The following values are used to figure out when the first layer is done.
        self.zOffsetLowestSeenMM = 1337.0
        self.zOffsetNotAtLowestCount = 0
        self.zOffsetTrackingStartTimeSec = 0.0
        self.zOffsetHasSeenPositiveExtrude = False
        self.RestorePrintProgressPercentage = False

        # Build the progress completion reported list.
        # Add an entry for each progress we want to report, not including 0 and 100%.
        # This list must be in order, from the lowest value to the highest.
        # See _getCurrentProgressFloat for usage.
        self.ProgressCompletionReported:List[ProgressCompletionReportItem] = []
        for x in range(1, 100):
            self.ProgressCompletionReported.append(ProgressCompletionReportItem(x, False))

         # Reset our anti spam times.
        self._clearSpammyEventContexts()

        # Ensure the bed cooldown watcher is stopped.
        self.BedCooldownWatcher.Stop()

        # The print cookie can only be None on class init.
        # We pass None so we don't call the PrintInfoManager, which might create a new print info on disk.
        # There might be a print info on disk we want to restore when the host connects to the printer.
        if printCookie is None:
            return

        # Always set the new print cookie
        self.PrintCookie = printCookie

        # See if we have an existing print that matches this cookie on disk.
        if PrintInfoManager.Get().GetPrintInfo(printCookie) is not None:
            self.Logger.info(f"Print Manager recovered a print info from disk matching cookie: {printCookie}")
            return

        # If we didn't find an existing print info, we need to make a new one.

        # Each time a print starts, we generate a fixed length random id to identify it.
        # This id is used to globally identify the print for the user, so it needs to have high entropy.
        printId = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(32))

        # Always make a new print info for this new print.
        # This is where we will store all of the vars for this print, and it's also written to disk if we need to recover the info.
        PrintInfoManager.Get().CreateNewPrintInfo(printCookie, printId)

    # If there is an valid print cookie and we can get the info, this returns it.
    # Returns None if there's no current print info.
    def GetPrintInfo(self) -> Optional[PrintInfo]:
        if self.PrintCookie is None or len(self.PrintCookie) == 0:
            return None
        return PrintInfoManager.Get().GetPrintInfo(self.PrintCookie)


    def GetPrintId(self) -> Optional[str]:
        pi = self.GetPrintInfo()
        if pi is None:
            return None
        return pi.GetPrintId()


    def GetPrintStartTimeSec(self) -> float:
        pi = self.GetPrintInfo()
        if pi is None:
            return 0.0
        return pi.GetLocalPrintStartTimeSec()


    def ReportPositiveExtrudeCommandSent(self) -> None:
        pass


    # Hints at if we are tracking a print or not.
    def IsTrackingPrint(self) -> bool:
        return self._IsPingTimerRunning()


    # Sets the cooldown threshold temp
    def SetBedCooldownThresholdTemp(self, tempC:float) -> None:
        self.BedCooldownWatcher.SetBedCooldownThresholdTemp(tempC)


    # A special case used by moonraker and bambu to restore the state of an ongoing print that we don't know of.
    # What we want to do is check moonraker or bambu's current state and our current state, to see if there's anything that needs to be synced.
    # Remember that we might be syncing because our service restarted during a print, or moonraker restarted, so we might already have
    # the correct context.
    #
    # Most importantly, we want to make sure the ping timer and thus Gadget get restored to the correct states.
    #
    def OnRestorePrintIfNeeded(self, isPrinting:bool, isPaused:bool, printCookie_CanBeNoneIfNoPrintIsActive:Optional[str]=None):

        # First, check if there's no active print currently.
        if (isPrinting is False and isPaused is False) or printCookie_CanBeNoneIfNoPrintIsActive is None:
            # There's no print running.
            if self._IsPingTimerRunning():
                self.Logger.info("Restore client sync state: There's no print running but the ping timers are running. Stopping them now.")
                self.StopTimers()
                return
            else:
                self.Logger.info("Restore client sync state: There's no print and none of the timers are running.")
                PrintInfoManager.Get().ClearAllPrintInfos()
                return


        # Next, we know there's an active print, so check if we already are tracking it's print cookie.
        # This is a scenario like the plugin didn't crash, but it lost the connection to the server, but it's back now.
        printCookie = printCookie_CanBeNoneIfNoPrintIsActive
        if self.PrintCookie is not None and self.PrintCookie == printCookie:
            # We have a print cookie and the cookie matches.
            # This means we just need to make sure the timer states are correct.
            if isPrinting:
                # There is an active print. Check our state.
                if self._IsPingTimerRunning():
                    self.Logger.info("Restore client sync state: We have the print cookie, detected an active print, and our timers are already running. So there's nothing to do.")
                    return
                else:
                    self.Logger.info("Restore client sync state: We have a print cookie, detected and active print, but the timers aren't running, so we will start them now.")
                    self.StartPrintTimers(False, None)
                    return
                    # We need to restore so we start the print timers.
            elif isPaused:
                # The print is paused, check our state.
                if self._IsPingTimerRunning():
                    self.Logger.info("Restore client sync state: We have a print cookie, detected a paused print, but our ping timers ARE RUNNING. Stopping them now.")
                    self.StopTimers()
                    return
                else:
                    self.Logger.info("Restore client sync state: We have a print cookie, detected a paused print, and timers aren't running. So there's nothing to do.")
                    return

        # If we are here, there's a print running or paused and we aren't tracking it currently.
        # This scenario probably is due to the plugin restarting.

        # This function will take the print cookie and (hopefully) recover an existing print info.
        # If it can't recover an existing print info, it will create a new one.
        self._RecoverOrRestForNewPrint(printCookie)

        # Set this flag so the first progress update will restore the progress to the current progress without
        # firing all of the progress points we missed.
        self.RestorePrintProgressPercentage = True

        # Make sure the timers are set correctly
        if isPrinting:
            # If we can get a duration, set the hours reported to that.
            hoursReportedInt = 0
            durationSec = self.GetCurrentDurationSecFloat()
            if durationSec > 0:
                # Convert seconds to hours, floor the value, make it an int.
                hoursReportedInt = int(math.floor(durationSec / 60.0 / 60.0))

            # Setup the timers, with hours reported, to make sure that the ping timer and Gadget are running.
            self.Logger.info("Restore client sync state: Restoring printing timer with existing duration of "+str(durationSec))
            self.StartPrintTimers(False, hoursReportedInt)
        else:
            # On paused, make sure they are stopped.
            self.StopTimers()
            self.Logger.info("Restore client sync state: Restoring into a paused print state.")


    def _cancelDelayedPause(self):
        if self.PauseThread is not None and self.PauseThread.is_alive():
            self.Logger.info("NOTIFICATION", "Cancelling delayed pause")
            self.PauseThread.stop()
            self.PauseThread = None

    # Only used for testing.
    def OnTest(self) -> None:
        if self._shouldIgnoreEvent():
            return
        self._sendEvent("test")


    # Only used for testing.
    def OnGadgetWarn(self) -> None:
        if self._shouldIgnoreEvent():
            return
        self._sendEvent("gadget-warning")


    # Only used for testing.
    def OnGadgetPaused(self) -> None:
        if self._shouldIgnoreEvent():
            return
        self._sendEvent("gadget-paused")


    # Fired when a print starts.
    # The print cookie is required. It's a per platform print unique string that's used to identify the print.
    # The string can be anything, but it must be a valid file name.
    # The string should also be unique between prints, but common for the same print. This allows us to pull up the print info for the same print if we crash or
    # or lose the printer connection.
    def OnStarted(self, printCookie:Optional[str], fileName:Optional[str]=None, fileSizeKBytes:int=0, totalFilamentUsageMm:int=0, totalFilamentWeightMg:int=0):
        # Validate
        if self._shouldIgnoreEvent(fileName):
            return
        if printCookie is None or len(printCookie) == 0:
            raise Exception("NotificationHandler OnStarted called with no print cookie.")

        # Since know we are starting a new print, we want to clear any existing print infos.
        # This is important for Moonraker, because there's no way to differentiate between prints beyond the filename.
        # So we have to use the file name, so we can still restore will work.
        # But in the case of printing the same print back to back, the print cookie will be the same.
        PrintInfoManager.Get().ClearAllPrintInfos()

        # This will reset the class for this new print and create the print info.
        self._RecoverOrRestForNewPrint(printCookie)

        # Update vars
        self._updateCurrentFileName(fileName)

        pi = self.GetPrintInfo()
        if pi is None:
            self.Logger.error("No print info returned after a new print started, this should not be possible.")
            return
        pi.SetFileSizeKBytes(fileSizeKBytes)
        pi.SetEstFilamentUsageMm(totalFilamentUsageMm)
        pi.SetEstFilamentWeightUsageMg(totalFilamentWeightMg)

        self.StartPrintTimers(True, None)
        self.CustomNotificationCounter = 0
        self._sendEvent(NotificationSender.EVENT_STARTED, progressOverwriteFloat=0.0)
        self.Logger.info(f"New print started; PrintId: {str(self.GetPrintId())} file:{str(pi.GetFileName())} size:{str(pi.GetFileSizeKBytes())} filament:{str(pi.GetEstFilamentUsageMm())}")


    # Fired when a new HMS (Health Monitoring System) code appears on a Bambu printer.
    # This is sent as a custom notification and is not rate-limited like Gcode notifications.
    def OnHmsNotification(self, message:str) -> None:
        self._sendEvent(NotificationSender.EVENT_CUSTOM, { NotificationSender.STATE_CUSTOM_EVENT_MESSAGE: message })


    # Triggered by a Gcode command
    def OnCustomNotification(self, message:str, body:Optional[str]=None, unlimited = False):
        state:Dict[str, str] = { NotificationSender.STATE_CUSTOM_EVENT_MESSAGE: message }
        if body:
            state[NotificationSender.STATE_CUSTOM_EVENT_DETAIL] = body
        if unlimited:
            self._sendEvent(NotificationSender.EVENT_CUSTOM, state)
        if self.CustomNotificationCounter < self.CustomNotificationLimit:
            self.CustomNotificationCounter += 1
            self._sendEvent(NotificationSender.EVENT_CUSTOM, state)
        elif self.CustomNotificationCounter == self.CustomNotificationLimit:
            self.CustomNotificationCounter += 1
            self._sendEvent(NotificationSender.EVENT_CUSTOM, { NotificationSender.STATE_CUSTOM_EVENT_MESSAGE: f"You reached the limit of {self.CustomNotificationLimit} Gcode notifications for this print"})


    # Fired when a print fails or is cancelled
    def OnFailed(self, fileName:Optional[str], durationSecStr:Optional[str]=None, reason:Optional[str]=None):
        if self._shouldIgnoreEvent(fileName):
            return
        self._updateCurrentFileName(fileName)
        self._updateToKnownDuration(durationSecStr)
        self.StopTimers()
        self.BedCooldownWatcher.Start()
        args = {}
        if reason is not None:
            args["Reason"] = reason
        # A cancel is a user action, not a fault. Send it as its own event so the apps can show the
        # correct text and so the "error" filter only covers actual faults. Anything without an
        # explicit cancel reason is treated as an error.
        event = NotificationSender.EVENT_CANCELLED if reason == "cancelled" else NotificationSender.EVENT_ERROR
        self._sendEvent(event, args)


    # Fired when a print done
    # For moonraker, these vars aren't known, so they are None
    def OnDone(self, fileName:Optional[str]=None, durationSecStr:Optional[str]=None):
        if self._shouldIgnoreEvent(fileName):
            return
        self._updateCurrentFileName(fileName)
        self._updateToKnownDuration(durationSecStr)
        self.StopTimers()
        self._cancelDelayedPause()
        self.BedCooldownWatcher.Start()
        self._sendEvent(NotificationSender.EVENT_DONE, terminalNotification=True)


    # Fired when a print is paused
    def OnPaused(self, fileName:Optional[str]=None):
        if self._shouldIgnoreEvent(fileName):
            return

        def firePause(delay:int, event:str):
            _self = self.PauseThread
            if _self is None:
                return
            self.Logger.info(f"Delaying pause for {delay} seconds")
            time.sleep(delay)
            if _self.stopped() is False:
                self.Logger.info("Delayed pause not stopped, executing")
                self._sendEvent(event)
                self.PauseThread = None
            else:
                self.Logger.info("Delayed pause was stopped, dropping")

        def scheduleSent(delay:int, event:str):
            if self.PauseThread is None or self.PauseThread.is_alive() is False:
                if delay == 0:
                    self._sendEvent(event)
                else:
                    self.PauseThread = StoppableThread(target=firePause, args=(delay, event))
                    self.PauseThread.start()
            else:
                self.Logger.error("Skipping pause, already scheduled")

        # Always update the file name.
        self._updateCurrentFileName(fileName)

        delay = 0
        event = NotificationSender.EVENT_PAUSED
        if Compat.IsMoonraker():
            # Because filament runout doesn't work, let's treat every pause as "interaction needed"
            # Also delay in case of timlapse photo the pause will be super short. If the print is resumed within the delay, drop the pause
            delay = 3
            event = NotificationSender.EVENT_USER_INTERACTION_NEEDED

        # See if there is a pause notification suppression set. If this is not null and it was recent enough
        # suppress the notification from firing.
        # If there is no suppression, or the suppression was older than 30 seconds, fire the notification.
        smartPauseInterface = Compat.GetSmartPauseInterface()
        if smartPauseInterface is not None:
            lastSuppressTimeSec = smartPauseInterface.GetAndResetLastPauseNotificationSuppressionTimeSec()
            if lastSuppressTimeSec is None or time.time() - lastSuppressTimeSec > 20.0:
                scheduleSent(delay, event)
            else:
                self.Logger.info("Not firing the pause notification due to a Smart Pause suppression.")
        else:
            scheduleSent(delay, event)

        # Stop the ping timer, so we don't report progress while we are paused.
        self.StopTimers()

    # Fired when a print is resumed
    def OnResume(self, fileName:Optional[str]=None):
        Sentry.Breadcrumb("OnResume called.", {"filename":fileName})
        if self._shouldIgnoreEvent(fileName):
            return

        # We sometimes get a resume event right after start, ignore
        if (time.time() - self.GetPrintStartTimeSec()) < 5:
            return


        self._cancelDelayedPause()
        self._updateCurrentFileName(fileName)
        self._sendEvent(NotificationSender.EVENT_RESUME)

        # Clear any spammy event contexts we have, assuming the user cleared any issues before resume.
        self._clearSpammyEventContexts()

        # Start the ping timer, to ensure it's running now.
        self.StartPrintTimers(False, None)


    # Fired when OctoPrint or the printer hits an error.
    def OnError(self, error:str):
        if self._shouldIgnoreEvent():
            return

        self.StopTimers()
        self._cancelDelayedPause()

        # Start the cooldown watcher because on it's first check, if the bed is already cool,
        # it won't fire any notifications.
        self.BedCooldownWatcher.Start()

        # This might be spammy from OctoPrint, so limit how often we bug the user with them.
        if self._shouldSendSpammyEvent("on-error"+str(error), 30.0) is False:
            return

        self._sendEvent(event=NotificationSender.EVENT_ERROR, args={"Error": error }, terminalNotification=True)


    # Fired when the waiting command is received from the printer.
    def OnWaiting(self):
        if self._shouldIgnoreEvent():
            return
        # Make this the same as the paused command.
        self.OnPaused()


    # Fired when we get a M600 command from the printer to change the filament
    def OnFilamentChange(self):
        if self._shouldIgnoreEvent():
            return
        # This event might fire over and over or might be paired with a filament change event.
        # In any case, we only want to fire it every so often.
        # It's important to use the same key to make sure we de-dup the possible OnUserInteractionNeeded that might fire second.
        if self._shouldSendSpammyEvent("user-interaction-needed", 5.0) is False:
            return

        # Otherwise, send it.
        self._sendEvent(NotificationSender.EVENT_FILAMENT_REQUIRED)


    # Fired when the printer needs user interaction to continue
    def OnUserInteractionNeeded(self):
        if self._shouldIgnoreEvent():
            return
        # This event might fire over and over or might be paired with a filament change event.
        # In any case, we only want to fire it every so often.
        # It's important to use the same key to make sure we de-dup the possible OnUserInteractionNeeded that might fire second.
        if self._shouldSendSpammyEvent("user-interaction-needed", 5.0) is False:
            return

        # Otherwise, send it.
        self._sendEvent(NotificationSender.EVENT_USER_INTERACTION_NEEDED)

    # Fired when the first layer is completed
    def OnFirstLayerDone(self):
        self._sendEvent(NotificationSender.EVENT_FIRST_LAYER_DONE)

     # Fired when the third layer is completed
    def OnThirdLayerDone(self):
        self._sendEvent(NotificationSender.EVENT_THIRD_LAYER_DONE)

     # Fired when the printer needs user interaction to continue
    def OnBeep(self):
        if self._shouldIgnoreEvent():
            return

        # This event might fire over and over or might be paired with a filament change event.
        # In any case, we only want to fire it every so often.
        # It's important to use the same key to make sure we de-dup the possible OnUserInteractionNeeded that might fire second.
        if self._shouldSendSpammyEvent("beep", 5.0) is False:
            return

        # Otherwise, send it.
        self._sendEvent(NotificationSender.EVENT_BEEP)


    # Fired when a print is making progress.
    def OnPrintProgress(self, octoPrintProgressInt:Optional[int], moonrakerProgressFloat:Optional[float]):
        if self._shouldIgnoreEvent():
            return

        # Always set the fallback progress, which will be used if something better can be found.
        # For moonraker, make sure to set the reported float. See _getCurrentProgressFloat about why.
        #
        # Note that in moonraker this is called very frequently, so this logic must be fast!
        #
        if octoPrintProgressInt is not None:
            self.FallbackProgressInt = octoPrintProgressInt
        elif moonrakerProgressFloat is not None:
            self.FallbackProgressInt = int(moonrakerProgressFloat)
            self.MoonrakerReportedProgressFloat_CanBeNone = moonrakerProgressFloat
        else:
            self.Logger.error("OnPrintProgress called with no args!")
            return

        # Get the computed print progress value. (see _getCurrentProgressFloat about why)
        computedProgressFloat = self._getCurrentProgressFloat()

        # If we are near the end of the print, start the final snap image capture system, to ensure we get a good "done" image.
        # This is a tricky number to set. For long prints, 1% can be very long, where as for quick prints we might not even see
        # all of the % updates.
        # First of all, don't bother unless the % complete is > 90% (this also guards from divide by 0)
        # if computedProgressFloat > 90.0 and self.FinalSnapObj is None:
        #     currentTimeSec = self.GetCurrentDurationSecFloat()
        #     estTimeRemainingSec = (self.GetCurrentDurationSecFloat() * 100.0) / computedProgressFloat
        #     estTimeUntilCompleteSec = estTimeRemainingSec - currentTimeSec
        #     # If we guess the print will be done in less than one minute, then start the final snap system.
        #     if estTimeUntilCompleteSec < 60.0:
        #         if self.FinalSnapObj is None:
        #             self.FinalSnapObj = FinalSnap(self)

        # Since we are computing the progress based on the ETA (see notes in _getCurrentProgressFloat)
        # It's possible we get duplicate ints or even progresses that goes back in time.
        # To account for this, we will make sure we only send the update for each progress update once.
        # We will also collapse many progress updates down to one event. For example, if the progress went from 5% -> 45%, we wil only report once for 10, 20, 30, and 40%.
        # We keep track of the highest progress that hasn't been reported yet.
        progressToSendFloat = 0.0
        for item in self.ProgressCompletionReported:
            # Keep going through the items until we find one that's over our current progress.
            # At that point, we are done.
            if item.Value() > computedProgressFloat:
                break

            # If we are over this value and it's not reported, we need to report.
            # Since these items are in order, the largest progress will always be overwritten.
            if item.Reported() is False:
                progressToSendFloat = item.Value()

            # Make sure this is marked reported.
            item.SetReported(True)

        # The first progress update after a restore won't fire any notifications. We use this update
        # to clear out all progress points under the current progress, so we don't fire them.
        # Do this before we check if we had something to send, so we always do this on the first tick
        # after a restore.
        if self.RestorePrintProgressPercentage:
            self.RestorePrintProgressPercentage = False
            return

        # Return if there is nothing to do.
        if progressToSendFloat < 0.1:
            return

        # It's important we send the "snapped" progress here (rounded to the tens place) because the service depends on it
        # to filter out % increments the user didn't want to get notifications for.
        self._sendEvent(NotificationSender.EVENT_PROGRESS, None, progressToSendFloat)


    # Fired every hour while a print is running
    def OnPrintTimerProgress(self):
        if self._shouldIgnoreEvent():
            return
        # This event is fired by our internal timer only while prints are running.
        # It will only fire every hour.

        # We send a duration, but that duration is controlled by OctoPrint and can be changed.
        # Since we allow the user to pick "every x hours" to be notified, it's easier for the server to
        # keep track if we just send an int as well.
        # Since this fires once an hour, every time it fires just add one.
        self.PingTimerHoursReported += 1

        self._sendEvent(NotificationSender.EVENT_TIME_PROGRESS, { "HoursCount": str(self.PingTimerHoursReported) })


    # Called by the bed cooldown watcher when the bed is done cooling down.
    def OnBedCooldownComplete(self, bedTempCelsius:float) -> None:
        # if self._shouldIgnoreEvent():
        #     return
        # self._sendEvent("bedcooldowncomplete", { "BedTempC": str(round(float(bedTempCelsius), 2)) })
        self.Logger.debug(f"Bed cooled down to {bedTempCelsius}, notfication not implemented -> skipping")


    # Assuming the current time is set at the start of the printer correctly.
    # This is also a live duration, if this is called once the print is over it will keep incrementing.
    def GetCurrentDurationSecFloat(self) -> float:
        pi = self.GetPrintInfo()
        if pi is None:
            return 0.0
        return float(time.time() - pi.GetLocalPrintStartTimeSec())


    # If we get a known duration from the platform, be sure to update it.
    def _updateToKnownDuration(self, durationSecStr:Optional[str]) -> None:
        # If the string is empty or None, return.
        # This is important for Moonraker
        if durationSecStr is None or len(durationSecStr) == 0:
            return

        # If we fail this logic don't kill the event.
        try:
            pi = self.GetPrintInfo()
            if pi is None:
                return
            pi.SetLocalPrintStartTimeSec(time.time() - float(durationSecStr))
        except Exception as e:
            Sentry.OnExceptionNoSend("_updateToKnownDuration exception", e)


    # Updates the current file name, if there is a new name to set.
    def _updateCurrentFileName(self, fileName:Optional[str]) -> None:
        # The None check is important for Moonraker
        if fileName is None or len(fileName) == 0:
            return
        pi = PrintInfoManager.Get().GetPrintInfo(self.PrintCookie)
        if pi is None:
            return
        pi.SetFileName(fileName)

    # Returns the current print progress as a float.
    def _getCurrentProgressFloat(self) -> float:
        # Special platform logic here!
        # Since this function is used to get the progress for all platforms, we need to do things a bit differently.

        # For moonraker, the progress is reported via websocket messages super frequently. There's no better way to compute the
        # progress (unlike OctoPrint) so we just want to use it, if we have it.
        #
        # We also don't want to constantly call GetPrintTimeRemainingEstimateInSeconds on moonraker, since it will result in a lot of RPC calls.
        if self.MoonrakerReportedProgressFloat_CanBeNone is not None:
            return self.MoonrakerReportedProgressFloat_CanBeNone

        # Then for OctoPrint, we will do the following logic to get a better progress.
        # OctoPrint updates us with a progress int, but it turns out that's not the same progress as shown in the web UI.
        # The web UI computes the progress % based on the total print time and ETA. Thus for our notifications to have accurate %s that match
        # the web UIs, we will also try to do the same.
        try:
            # Try to get the print time remaining, which will use smart ETA plugins if possible.
            ptrSec = self.PrinterStateInterface.GetPrintTimeRemainingEstimateInSeconds()
            # If we can't get the ETA, default to OctoPrint's value.
            if ptrSec == -1:
                return float(self.FallbackProgressInt)

            # Compute the total print time (estimated) and the time thus far
            currentDurationSecFloat = self.GetCurrentDurationSecFloat()
            totalPrintTimeSec = currentDurationSecFloat + ptrSec

            # Sanity check for / 0
            if totalPrintTimeSec == 0:
                return float(self.FallbackProgressInt)

            # Compute the progress
            printProgressFloat = float(currentDurationSecFloat) / float(totalPrintTimeSec) * float(100.0)
            self.Logger.info(f"Computing progress: currentDurationSecFloat={currentDurationSecFloat} totalPrintTimeSec={totalPrintTimeSec}")

            # Bounds check
            printProgressFloat = max(printProgressFloat, 0.0)
            printProgressFloat = min(printProgressFloat, 100.0)

            # Return the computed value.
            return printProgressFloat

        except Exception as e:
            Sentry.OnExceptionNoSend("_getCurrentProgressFloat failed to compute progress.", e)

        # On failure, default to what OctoPrint has reported.
        return float(self.FallbackProgressInt)


    # Sends the event
    # Returns True on success, otherwise False
    def _sendEvent(self, event:str, args:Optional[Dict[str,str]]=None, progressOverwriteFloat:Optional[float]=None, terminalNotification=False):
        # Push the work off to a thread so we don't hang OctoPrint's plugin callbacks.
        thread = threading.Thread(target=self._sendEventThreadWorker, args=(event, args, progressOverwriteFloat, terminalNotification, ), name="NotificationsHandler._sendEvent")
        thread.start()
        return True


    # Sends the event
    # Returns True on success, otherwise False
    def _sendEventThreadWorker(self, event:str, args:Optional[Dict[str,str]]=None, progressOverwriteFloat:Optional[float]=None, terminalNotification=False):
        try:
            # Build the common even args.
            requestArgs = self.BuildCommonEventArgs(event, args, progressOverwriteFloat=progressOverwriteFloat)

            # Handle the result indicating we don't have the proper var to send yet.
            if requestArgs is None:
                self.Logger.info("NotificationsHandler didn't send the "+str(event)+" event because we don't have the proper id and key yet.")
                return False

            # Break out the response
            args = requestArgs[0]

            # Use fairly aggressive retry logic on notifications if they fail to send.
            # This is important because they power some of the other features of OctoApp now, so having them as accurate as possible is ideal.
            attempts = 0
            while attempts < 3:
                attempts += 1
                statusCode = 0
                try:
                    # Since we are sending the snapshot, we must send a multipart form.
                    # Thus we must use the data and files fields, the json field will not work.
                    #r = requests.post(eventApiUrl, data=args, files=files, timeout=5*60)
                    self.Logger.info(f"Sending {event} ({args})")
                    self.NotificationSender.SendNotification(event=event, state=args)

                    # If success
                    return True

                except Exception as e:
                    # We must try catch the connection because sometimes it will throw for some connection issues, like DNS errors, server not connectable, etc.
                    Sentry.ExceptionNoSend("Failed to send notification due to a connection error. ", e)

                # On failure, log the issue.
                self.Logger.error(f"NotificationsHandler failed to send event {str(event)}. Code:{str(statusCode)}. Waiting and then trying again.")

                # If the error is in the 400 class, don't retry since these are all indications there's something
                # wrong with the request, which won't change. But we don't want to include anything above or below that.
                if statusCode > 399 and statusCode < 500:
                    return False

                # We have quite a few reties and back off a decent amount. As said above, we want these to be reliable as possible, even if they are late.
                # We want the first few retires to be quick, so the notifications happens ASAP. This will help in teh case where the server is updating, it should be
                # back withing 2-4 seconds, but 20 is a good time to wait.
                # If it's still failing, we want to allow the system some time to do a do a fail over or something, thus we give the retry timer more time.
                if attempts < 1: # Attempt 1 and 2 will wait 20 seconds.
                    time.sleep(20)
                else: # Attempt 3, 4, 5 will wait longer.
                    time.sleep(60 * attempts)

            # We never sent it successfully.
            self.Logger.error("NotificationsHandler failed to send event "+str(event)+" due to a network issues after many retries.")

        except Exception as e:
            Sentry.ExceptionNoSend("NotificationsHandler failed to send event code "+str(event), e)
        finally:
            if terminalNotification:
                PrintInfoManager.Get().ClearAllPrintInfos()

        return False


    # Used by notifications and gadget to build a common event args.
    # Returns an array of [args, files] which are ready to be used in the request.
    # The args and files will always contain any information that can be gathered at the time of the call.
    # Returns None if we don't have the printer id or octokey yet.
    def BuildCommonEventArgs(self, event:str, args:Optional[Dict[str,str]]=None, progressOverwriteFloat:Optional[float]=None, snapshotResizeParams:Optional[SnapshotResizeParams]=None, useFinalSnapSnapshot=False) -> Tuple[Optional[Dict[str,str]], Optional[Dict[str, Tuple[str, ByteLikeOrMemoryView]]]]:
        # Default args
        if args is None:
            args = {}

        # Define files so we can return an empty dict on any failures.
        files:Dict[str, Tuple[str, ByteLikeOrMemoryView]] = {}

        # Get the print info if there is a current print.
        # Remember that some notifications will fire when there's no print running, like if OctoPrint loses it's connection to the printer while idle.
        pi = PrintInfoManager.Get().GetPrintInfo(self.PrintCookie)
        if pi is not None:
            args[NotificationSender.STATE_PRINT_ID] = pi.GetPrintId()
            args[NotificationSender.STATE_FILE_NAME] = str(pi.GetFileName()).rsplit('/', maxsplit=1)[-1]
            args[NotificationSender.STATE_FILE_PATH] = str(pi.GetFileName())
            args["FileSizeKb"] = str(pi.GetFileSizeKBytes())
            args["FilamentUsageMm"] = str(pi.GetEstFilamentUsageMm())
            args["FilamentWeightMg"] = str(pi.GetEstFilamentWeightUsageMg())
        else:
            self.Logger.error("NotificationsHandler failed to get the print info for the current print.", {"Cookie": self.PrintCookie, "Event": event})

        # Add the required vars
        args["Event"] = event

        # Always include the ETA, note this will be -1 if the time is unknown.
        timeRemainEstStr =  str(self.PrinterStateInterface.GetPrintTimeRemainingEstimateInSeconds())
        args[NotificationSender.STATE_TIME_REMAINING_SEC] = timeRemainEstStr

        # Always include the layer height, if it can be gotten from the platform.
        currentLayer, totalLayers = self.PrinterStateInterface.GetCurrentLayerInfo()
        if currentLayer is not None and totalLayers is not None:
            # Note both of these values can be 0 if the layer counts aren't known yet!
            args["CurrentLayer"] = str(currentLayer)
            args["TotalLayers"] = str(totalLayers)

        # Always add the current progress
        # -> int to round -> to string for the API.
        # Allow the caller to overwrite the progress we report. This allows the progress update to snap the progress to a hole 10s value.
        progressFloat = 0.0
        if progressOverwriteFloat is not None:
            progressFloat = progressOverwriteFloat
        else:
            progressFloat = self._getCurrentProgressFloat()

        args[NotificationSender.STATE_PROGRESS_PERCENT] = str(int(progressFloat))

        # Always add the current duration
        args[NotificationSender.STATE_DURATION_SEC] = str(self.GetCurrentDurationSecFloat())

        # Error state? Copy into the normal error field
        error = args.get("Error", None)
        if error is not None:
            args[NotificationSender.STATE_ERROR] = error

        return (args, files)


    # Stops any running timer, be it the progress timer, the Gadget timer, or something else.
    def StopTimers(self) -> None:
        # Capture locally & Stop
        progressTimer = self.ProgressTimer
        self.ProgressTimer = None
        if progressTimer is not None:
            progressTimer.Stop()

        # Stop Gadget From Watching
        # self.Gadget.StopWatching()


    def StopFirstLayerTimer(self) -> None:
        # Capture locally & Stop
        firstLayerTimer = self.FirstLayerTimer
        self.FirstLayerTimer = None
        if firstLayerTimer is not None:
            firstLayerTimer.Stop()


    # Starts all print timers, including the progress time, Gadget, and the first layer watcher.
    def StartPrintTimers(self, resetHoursReported:bool, restoreActionSetHoursReported:Optional[int]=None) -> None:
        # First, stop any timer that's currently running.
        self.StopTimers()

        # Make sure the hours flag is cleared when we start a new timer.
        if resetHoursReported:
            self.PingTimerHoursReported = 0

        # If this is a restore, set the value
        if restoreActionSetHoursReported is not None:
            self.PingTimerHoursReported = int(restoreActionSetHoursReported)

        # Setup the progress timer
        intervalSec = 60 * 60 # Fire every hour.
        timer = RepeatTimer(self.Logger, "Notifications-TimedProgress", intervalSec, self.ProgressTimerCallback)
        timer.start()
        self.ProgressTimer = timer

        # Start Gadget From Watching
        # self.Gadget.StartWatching()


    # Let's the caller know if the ping timer is running, and thus we are tracking a print.
    def _IsPingTimerRunning(self) -> bool:
        return self.ProgressTimer is not None


    # Fired when the ping timer fires.
    def ProgressTimerCallback(self) -> None:

        # Double check the state is still printing before we send the notification.
        # Even if the state is paused, we want to stop, since the resume command will restart the timers
        if self.PrinterStateInterface.ShouldPrintingTimersBeRunning() is False:
            self.Logger.info("Notification progress timer state doesn't seem to be printing, stopping timer.")
            self.StopTimers()
            return

        # Fire the event.
        self.OnPrintTimerProgress()

    # Only allows possibly spammy events to be sent every x minutes.
    # Returns true if the event can be sent, otherwise false.
    def _shouldSendSpammyEvent(self, eventName:str, minTimeBetweenMinutesFloat:float) -> bool:
        with self.SpammyEventLock:
            # Check if the event has been added to the dict yet.
            if eventName not in self.SpammyEventTimeDict:
                # No event added yet, so add it now.
                self.SpammyEventTimeDict[eventName] = SpammyEventContext()
                return True

            # Check how long it's been since the last notification was sent.
            # If it's less than 5 minutes, don't allow the event to send.
            if self.SpammyEventTimeDict[eventName].ShouldSendEvent(minTimeBetweenMinutesFloat) is False:
                return False

            # Report we are sending an event and return true.
            self.SpammyEventTimeDict[eventName].ReportEventSent()
            return True


    def _clearSpammyEventContexts(self) -> None:
        with self.SpammyEventLock:
            self.SpammyEventTimeDict = {}


    # Very rarely, we want to ignore some notifications based on different metrics.
    # A filename can be passed to check, if not, the current file name will be used.
    def _shouldIgnoreEvent(self, fileName:Optional[str]=None) -> bool:
        # Check if there was a file name passed, if so use it.
        # If not, fall back to the current file name.
        # If there is neither, dont ignore.
        if fileName is None or len(fileName) == 0:
            pi = self.GetPrintInfo()
            if pi is None:
                return False
            fileName = pi.GetFileName()
            if fileName is None or len(fileName) == 0:
                return False
        # One case we want to ignore is when the continuous print plugin uses it's "placeholder" .gcode files.
        # These files are used between prints to hold the printer before a new print starts.
        # The events are listed here, and the file name will be 'continuousprint_finish.gcode' for example.
        # https://github.com/smartin015/continuousprint/blob/bfb2c13da2ebbe0bfbfaa90f62a91db332c43b1b/continuousprint/data/__init__.py#L62
        fileNameLower = fileName.lower()
        if fileNameLower.startswith("continuousprint_"):
            self.Logger.info("Ignoring notification because it's a continuous print place holder file. "+str(fileName))
            return True
        return False

class StoppableThread(threading.Thread):
    """Thread class with a stop() method. The thread itself has to check
    regularly for the stopped() condition."""

    def __init__(self,  *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

class SpammyEventContext:

    def __init__(self):
        self.ConcurrentCount = 0
        self.LastSentTimeSec = 0
        self.ReportEventSent()


    def ReportEventSent(self):
        self.ConcurrentCount += 1
        self.LastSentTimeSec = time.time()


    def ShouldSendEvent(self, baseTimeIntervalMinutesFloat:float) -> bool:
        # Figure out what the delay multiplier should be.
        delayMultiplier = 1

        # For the first 3 events, don't back off.
        if self.ConcurrentCount > 3:
            delayMultiplier = self.ConcurrentCount

        # Sanity check.
        delayMultiplier = max(delayMultiplier, 1)

        # Ensure we don't try to delay too long.
        # Most of these timers are base intervals of 5 minutes, so 288 is one every 24 hours.
        delayMultiplier = min(delayMultiplier, 288)

        timeSinceLastSendSec = time.time() - self.LastSentTimeSec
        sendIntervalSec = baseTimeIntervalMinutesFloat * 60.0
        if timeSinceLastSendSec > sendIntervalSec * delayMultiplier:
            return True
        return False
