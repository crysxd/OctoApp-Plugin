import logging
import traceback
from typing import Any, Dict, List, Optional

from octoapp.mdns import MDns
from octoapp.sentry import Sentry
from octoapp.deviceid import DeviceId
from octoapp.hostcommon import HostCommon
from octoapp.httpsessions import HttpSessions
from octoapp.printinfo import PrintInfoManager
from octoapp.notificationshandler import NotificationsHandler
from octoapp.Proto.ServerHost import ServerHost
from octoapp.compat import Compat
from octoapp.appsstorage import AppStorageHelper
from octoapp.firebaseappstorage import FirebaseAppStorage
from octoapp.logging import TaggedLoggingAdapter
from octoapp.interfaces import IHostCommandHandler, IPopUpInvoker, IStateChangeHandler

from linux_host.config import Config
from linux_host.secrets import Secrets
from linux_host.version import Version
from linux_host.logger import LoggerInit

from .elegooappstorage import ElegooFirebaseIdentity
from .elegoowebsocketmux import ElegooWebsocketMux
from .elegooclient import ElegooClient
from .elegoofilemanager import ElegooFileManager
from .elegoostatetranslater import ElegooStateTranslator

# This file is the main host for the elegoo os service.
class ElegooHost(IHostCommandHandler, IPopUpInvoker, IStateChangeHandler):

    def __init__(self, configDir:str, logDir:str, devConfig:Optional[Dict[str, Any]]) -> None:
        # When we create our class, make sure all of our core requirements are created.
        self.Secrets:Secrets = None #pyright: ignore[reportAttributeAccessIssue]
        self.NotificationHandler:NotificationsHandler = None #pyright: ignore[reportAttributeAccessIssue]

        # Let the compat system know this is an elegoo host.
        Compat.SetIsElegooOs(True)

        try:
            # First, we need to load our config.
            # Note that the config MUST BE WRITTEN into this folder, that's where the setup installer is going to look for it.
            # If this fails, it will throw.
            self.Config = Config(configDir)

            # Setup the logger.
            logLevelOverride = self.GetDevConfigStr(devConfig, "LogLevel")
            self.RawLogger = LoggerInit.GetLogger(self.Config, logDir, logLevelOverride)
            self.Logger = TaggedLoggingAdapter(self.RawLogger, "MAIN")
            self.Config.SetLogger(self.Logger)

            # Give the logger to Sentry ASAP.
            Sentry.SetLogger(self.RawLogger)

        except Exception as e:
            tb = traceback.format_exc()
            print("Failed to init Elegoo Host! "+str(e) + "; "+str(tb))
            # Raise the exception so we don't continue.
            raise


    def RunBlocking(self, configPath:str, localStorageDir:str, repoRoot:str, devConfig:Optional[Dict[str, Any]]) -> None:
        # Do all of this in a try catch, so we can log any issues before exiting
        try:
            self.Logger.info("#############################################")
            self.Logger.info("#### OctoApp Elegoo OS Connect Starting #####")
            self.Logger.info("#############################################")

            # Find the version of the plugin, this is required and it will throw if it fails.
            pluginVersionStr = Version.GetPluginVersion(repoRoot)
            self.Logger.info("Plugin Version: %s", pluginVersionStr)

            # Setup the HttpSession cache early, so it can be used whenever
            HttpSessions.Init(TaggedLoggingAdapter(self.RawLogger, "HTTPSESSION"))

            # As soon as we have the plugin version, setup Sentry
            # Enabling profiling and no filtering, since we are the only PY in this process.
            Sentry.Setup(pluginVersionStr, "elegoo", devConfig is not None, enableProfiling=True, filterExceptionsByPackage=False, restartOnCantCreateThreadBug=True)

            # Before the first time setup, we must also init the Secrets class and do the migration for the printer id and private key, if needed.
            self.Secrets = Secrets(TaggedLoggingAdapter(self.RawLogger, "SECRETS"), localStorageDir)

            # Now, detect if this is a new instance and we need to init our global vars. If so, the setup script will be waiting on this.
            self.DoFirstTimeSetupIfNeeded()

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

            # Setup the state translator and notification handler
            stateTranslator = ElegooStateTranslator(TaggedLoggingAdapter(self.RawLogger, "ELEGOOTRANSLATOR"))
            self.NotificationHandler = NotificationsHandler(self.Logger, stateTranslator)
            self.NotificationHandler.SetBedCooldownThresholdTemp(self.Config.GetFloatRequired(Config.GeneralSection, Config.GeneralBedCooldownThresholdTempC, Config.GeneralBedCooldownThresholdTempCDefault))
            stateTranslator.SetNotificationHandler(self.NotificationHandler)

            # Init the file manager
            ElegooFileManager.Init(TaggedLoggingAdapter(self.RawLogger, "FILEMANAGER"))

            # Setup and start the Elegoo Client
            websocketMux = ElegooWebsocketMux(TaggedLoggingAdapter(self.RawLogger, "WEBSOCKETMUX"))
            ElegooClient.Init(TaggedLoggingAdapter(self.RawLogger, "CLIENT"), self.Config, pluginVersionStr, stateTranslator, websocketMux, ElegooFileManager.Get())

            # Init app storage"
            AppStorageHelper.Init(TaggedLoggingAdapter(self.RawLogger, "APPS"), FirebaseAppStorage(TaggedLoggingAdapter(self.RawLogger, "DATABASE"), pluginVersionStr, ElegooFirebaseIdentity(TaggedLoggingAdapter(self.RawLogger, "IDENTITY"))))

            # Now start the main runner!
            ElegooClient.Get().RunBlocking()
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
    def DoFirstTimeSetupIfNeeded(self):
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
    def GetPrinterId(self):
        return self.Secrets.GetPrinterId()


    # Returns None if no private id has been set.
    def GetPrivateKey(self):
        return self.Secrets.GetPrivateKey()


    # Tries to load a dev config option as a string.
    # If not found or it fails, this return None
    def GetDevConfigStr(self, devConfig:Optional[Dict[str, Any]], value:str) -> Optional[str]:
        if devConfig is None:
            return None
        if value in devConfig:
            v = devConfig[value]
            if v is not None and len(v) > 0 and v != "None":
                return v
        return None


    # This is a destructive action! It will remove the printer id and private key from the system and restart the plugin.
    def Rekey(self, reason:str):
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
        ElegooClient.Get().SendFrontendPopupMsg(title, text, msgType, actionText, actionLink, showForSec, onlyShowIfLoadedViaOeBool)


    #
    # StatusChangeHandler Interface - Called by the OctoApp logic when the server connection has been established.
    #
    def OnPrimaryConnectionEstablished(self, octoKey:str, connectedAccounts:List[str]) -> None:
        self.Logger.info("Primary Connection To OctoApp Established - We Are Ready To Go!")


    #
    # StatusChangeHandler Interface - Called by the OctoApp logic when a plugin update is required for this client.
    #
    def OnPluginUpdateRequired(self) -> None:
        self.Logger.error("!!! A Plugin Update Is Required -- If This Plugin Isn't Updated It Might Stop Working !!!")
        self.Logger.error("!!! Please SSH into the device running this plug-in and run the update script or update the docker container!  !!!")


    #
    # StatusChangeHandler Interface - Called by the OctoApp handshake when a rekey is required.
    #
    def OnRekeyRequired(self) -> None:
        self.Rekey("Handshake Failed")


    #
    # Command Host Interface - Called by the command handler, when called the plugin must clear it's keys and restart to generate new ones.
    #
    def OnRekeyCommand(self) -> bool:
        self.Rekey("Command")
        return True
