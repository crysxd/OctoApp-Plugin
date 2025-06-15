import logging
import traceback
import time
from typing import Any, Dict, List, Optional

from octoapp.mdns import MDns
from octoapp.sentry import Sentry
from octoapp.deviceid import DeviceId
from octoapp.hostcommon import HostCommon
from octoapp.httpsessions import HttpSessions
from octoapp.printinfo import PrintInfoManager
from octoapp.notificationshandler import NotificationsHandler
from octoapp.octohttprequest import OctoHttpRequest
from octoapp.Proto.ServerHost import ServerHost
from octoapp.compat import Compat
from octoapp.interfaces import IHostCommandHandler, IPopUpInvoker, IStateChangeHandler
from octoapp.logging import TaggedLoggingAdapter
from octoapp.appsstorage import AppStorageHelper

from linux_host.config import Config
from linux_host.secrets import Secrets
from linux_host.version import Version
from linux_host.logger import LoggerInit

from .bambucloud import BambuCloud
from .bambuclient import BambuClient
from .bambustatetranslater import BambuStateTranslator
from .bambuappstorage import BambuAppStorage
from .bambudatabase import BambuFtpDatabase

# This file is the main host for the bambu service.
class BambuHost(IHostCommandHandler, IPopUpInvoker, IStateChangeHandler):

    def __init__(self, configDir:str, logDir:str, devConfig:Optional[Dict[str,str]]) -> None:
        # When we create our class, make sure all of our core requirements are created.
        self.Secrets:Secrets = None #pyright: ignore[reportAttributeAccessIssue]
        self.NotificationHandler:Optional[NotificationsHandler] = None

        # Let the compat system know this is an Bambu host.
        Compat.SetIsBambu(True)

        try:
            # First, we need to load our config.
            # Note that the config MUST BE WRITTEN into this folder, that's where the setup installer is going to look for it.
            # If this fails, it will throw.
            self.Config = Config(configDir)

            # Setup the logger.
            logLevelOverride_CanBeNone = self.GetDevConfigStr(devConfig, "LogLevel")
            self.RawLogger = LoggerInit.GetLogger(self.Config, logDir, logLevelOverride_CanBeNone)
            self.Logger = TaggedLoggingAdapter(self.RawLogger, "MAIN")
            self.Config.SetLogger(self.Logger)

            # Give the logger to Sentry ASAP.
            Sentry.SetLogger(self.RawLogger)

        except Exception as e:
            tb = traceback.format_exc()
            print("Failed to init Bambu Host! "+str(e) + "; "+str(tb))
            # Raise the exception so we don't continue.
            raise


    def RunBlocking(self, configPath:str, localStorageDir:str, repoRoot:str, devConfig:Optional[Dict[str,str]]) -> None:
        # Do all of this in a try catch, so we can log any issues before exiting
        try:
            self.Logger.info("#########################################")
            self.Logger.info("#### OctoApp Bambu Connect Starting #####")
            self.Logger.info("#########################################")

            # Find the version of the plugin, this is required and it will throw if it fails.
            pluginVersionStr = Version.GetPluginVersion(repoRoot)
            self.Logger.info("Plugin Version: %s", pluginVersionStr)

            # Setup the HttpSession cache early, so it can be used whenever
            HttpSessions.Init(TaggedLoggingAdapter(self.RawLogger, "HTTPSESSION"))

            # As soon as we have the plugin version, setup Sentry
            # Enabling profiling and no filtering, since we are the only PY in this process.
            Sentry.Setup(pluginVersionStr, "bambu", devConfig is not None, enableProfiling=True, filterExceptionsByPackage=False, restartOnCantCreateThreadBug=True)

            # Before the first time setup, we must also init the Secrets class and do the migration for the printer id and private key, if needed.
            self.Secrets = Secrets(TaggedLoggingAdapter(self.RawLogger, "SECRETS"), localStorageDir)

            # Now, detect if this is a new instance and we need to init our global vars. If so, the setup script will be waiting on this.
            self.DoFirstTimeSetupIfNeeded()

            # Get our required vars
            printerId = self.GetPrinterId()

            if printerId is None:
                raise Exception("Printer ID or Private Key is None, this should never happen!")

            # Set the printer ID into sentry.
            Sentry.SetPrinterId(printerId)

            # Init "database"
            database = BambuFtpDatabase(TaggedLoggingAdapter(self.RawLogger, "FTP"), printerId, pluginVersionStr, self.Config)
            AppStorageHelper.Init(TaggedLoggingAdapter(self.RawLogger, "APPS"), BambuAppStorage(database))

            # Unpack any dev vars that might exist
            DevLocalServerAddress_CanBeNone = self.GetDevConfigStr(devConfig, "LocalServerAddress")
            if DevLocalServerAddress_CanBeNone is not None:
                self.Logger.warning("~~~ Using Local Dev Server Address: %s ~~~", DevLocalServerAddress_CanBeNone)

            # Init the mdns client
            MDns.Init(TaggedLoggingAdapter(self.RawLogger, "MDNS"), localStorageDir)

            # Init device id
            DeviceId.Init(TaggedLoggingAdapter(self.RawLogger, "DEVICEID"))

            # Setup the print info manager.
            PrintInfoManager.Init(TaggedLoggingAdapter(self.RawLogger, "PRINTINFO"), localStorageDir)

            # But we still want to set the "local OctoPrint port" to 80, because that's the default port it will try for relative URLs.
            # Relative URLs for Bambu only come from the alternative webcam streaming system, which the user might be trying to access a webcam stream from this device.
            # If they don't specify a IP (or localhost) and port, then we will default all relative URLs to the "local OctoPrint port" value.
            OctoHttpRequest.SetLocalHttpProxyPort(80)
            OctoHttpRequest.SetLocalOctoPrintPort(80)
            OctoHttpRequest.SetLocalHttpProxyIsHttps(False)

            # Setup the state translator and notification handler
            stateTranslator = BambuStateTranslator(TaggedLoggingAdapter(self.RawLogger, "BAMBUTRANSLATOR"))
            self.NotificationHandler = NotificationsHandler(self.Logger, stateTranslator)
            self.NotificationHandler.SetBedCooldownThresholdTemp(self.Config.GetFloatRequired(Config.GeneralSection, Config.GeneralBedCooldownThresholdTempC, Config.GeneralBedCooldownThresholdTempCDefault))
            stateTranslator.SetNotificationHandler(self.NotificationHandler)

            # Setup the cloud if it's setup in the config.
            BambuCloud.Init(TaggedLoggingAdapter(self.RawLogger, "BAMBUCLOUD"), self.Config)

            # Setup and start the Bambu Client
            BambuClient.Init(TaggedLoggingAdapter(self.RawLogger, "CLIENT"), self.Config, stateTranslator)

            # Now start the main runner!
            BambuClient.Get().RunBlocking()

            time.sleep(120)
        except Exception as e:
            Sentry.OnException("!! Exception thrown out of main host run function.", e)

        # Allow the loggers to flush before we exit
        try:
            self.Logger.info("###########################")
            self.Logger.info("#### OctoApp Exiting ######")
            self.Logger.info("###########################")
            logging.shutdown()
        except Exception as e:
            print("Exception in logging.shutdown "+str(e))


    # Ensures all required values are setup and valid before starting.
    def DoFirstTimeSetupIfNeeded(self) -> None:
        # Try to get the printer id from the config.
        printerId = self.GetPrinterId()
        if HostCommon.IsPrinterIdValid(printerId) is False:
            if printerId is None:
                self.Logger.info("No printer id was found, generating one now!")
            else:
                self.Logger.info("An invalid printer id was found [%s], regenerating!", str(printerId))

            # Make a new, valid, key
            printerId = HostCommon.GeneratePrinterId()

            # Save it
            self.Secrets.SetPrinterId(printerId)
            self.Logger.info("New printer id created: %s", printerId)


    # Returns None if no printer id has been set.
    def GetPrinterId(self) -> Optional[str]:
        return self.Secrets.GetPrinterId()


    # Returns None if no private id has been set.
    def GetPrivateKey(self) -> Optional[str]:
        return self.Secrets.GetPrivateKey()


    # Tries to load a dev config option as a string.
    # If not found or it fails, this return None
    def GetDevConfigStr(self, devConfig: Optional[Dict[str, Any]], value: str) -> Optional[str]:
        if devConfig is None:
            return None
        if value in devConfig:
            v = devConfig[value]
            if v is not None and len(v) > 0 and v != "None":
                return v
        return None


    # This is a destructive action! It will remove the printer id and private key from the system and restart the plugin.
    def Rekey(self, reason:str) -> None:
        #pylint: disable=logging-fstring-interpolation
        self.Logger.error(f"HOST REKEY CALLED {reason} - Clearing keys...")
        # It's important we clear the key, or we will reload, fail to connect, try to rekey, and restart again!
        self.Secrets.SetPrinterId(None)
        self.Secrets.SetPrivateKey(None)
        self.Logger.error("Key clear complete, restarting plugin.")
        HostCommon.RestartPlugin()


    # UiPopupInvoker Interface function - Sends a UI popup message for various uses.
    # Must stay in sync with the OctoPrint handler!
    # title - string, the title text.
    # text  - string, the message.
    # type  - string, [notice, info, success, error] the type of message shown.
    # actionText - string, if not None or empty, this is the text to show on the action button or text link.
    # actionLink - string, if not None or empty, this is the URL to show on the action button or text link.
    # onlyShowIfLoadedViaOeBool - bool, if set, the message should only be shown on browsers loading the portal from OE.
    def ShowUiPopup(self, title:str, text:str, msgType:str, actionText:Optional[str], actionLink:Optional[str], showForSec:int, onlyShowIfLoadedViaOeBool:bool) -> None:
        # This isn't supported on Bambu
        pass


    #
    # StatusChangeHandler Interface - Called by the OctoEverywhere logic when the server connection has been established.
    #
    def OnPrimaryConnectionEstablished(self, octoKey:str, connectedAccounts:List[str]) -> None:
        self.Logger.info("Primary Connection To OctoApp Established - We Are Ready To Go!")


    #
    # StatusChangeHandler Interface - Called by the OctoEverywhere logic when a plugin update is required for this client.
    #
    def OnPluginUpdateRequired(self) -> None:
        self.Logger.error("!!! A Plugin Update Is Required -- If This Plugin Isn't Updated It Might Stop Working !!!")
        self.Logger.error("!!! Please use the update manager in Mainsail of Fluidd to update this plugin         !!!")


    #
    # StatusChangeHandler Interface - Called by the OctoEverywhere handshake when a rekey is required.
    #
    def OnRekeyRequired(self) -> None:
        self.Rekey("Handshake Failed")


    #
    # Command Host Interface - Called by the command handler, when called the plugin must clear it's keys and restart to generate new ones.
    #
    def OnRekeyCommand(self) -> bool:
        self.Rekey("Command")
        return True
