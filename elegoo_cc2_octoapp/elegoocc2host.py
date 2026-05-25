import logging
import traceback
from typing import Any, Dict, List, Optional

from linux_host.config import Config
from linux_host.localwebapi import LocalWebApi
from linux_host.logger import LoggerInit
from linux_host.secrets import Secrets
from linux_host.version import Version

from octoapp.appsstorage import AppStorageHelper
from octoapp.compat import Compat
from octoapp.deviceid import DeviceId
from octoapp.firebaseappstorage import FirebaseAppStorage
from octoapp.hostcommon import HostCommon
from octoapp.httpsessions import HttpSessions
from octoapp.interfaces import IHostCommandHandler, IPopUpInvoker, IStateChangeHandler
from octoapp.linkhelper import LinkHelper
from octoapp.localip import LocalIpHelper
from octoapp.logging import TaggedLoggingAdapter
from octoapp.mdns import MDns
from octoapp.notificationshandler import NotificationsHandler
from octoapp.printinfo import PrintInfoManager
from octoapp.sentry import Sentry

from .elegoocc2appstorage import ElegooCc2FirebaseIdentity
from .elegoocc2client import ElegooCc2Client
from .elegoocc2filemanager import ElegooCc2FileManager
from .elegoocc2statetranslater import ElegooCc2StateTranslator

class ElegooCc2Host(IHostCommandHandler, IPopUpInvoker, IStateChangeHandler):

    def __init__(self, configDir:str, logDir:str, devConfig:Optional[Dict[str, Any]]) -> None:
        self.Secrets:Secrets = None #pyright: ignore[reportAttributeAccessIssue]
        self.NotificationHandler:NotificationsHandler = None #pyright: ignore[reportAttributeAccessIssue]

        Compat.SetIsElegooCc2(True)

        try:
            self.Config = Config(configDir)

            logLevelOverride = self.GetDevConfigStr(devConfig, "LogLevel")
            self.RawLogger = LoggerInit.GetLogger(self.Config, logDir, logLevelOverride)
            self.Logger = TaggedLoggingAdapter(self.RawLogger, "MAIN")
            self.Config.SetLogger(self.Logger)

            Sentry.SetLogger(self.RawLogger)
        except Exception as e:
            tb = traceback.format_exc()
            print("Failed to init Elegoo CC2 Host! "+str(e) + "; "+str(tb))
            raise


    def RunBlocking(self, configPath:str, localStorageDir:str, repoRoot:str, devConfig:Optional[Dict[str, Any]]) -> None:
        try:
            self.Logger.info("######################################################")
            self.Logger.info("###### OctoApp Elegoo CC2 Connect Starting ##########")
            self.Logger.info("######################################################")

            pluginVersionStr = Version.GetPluginVersion(repoRoot)
            self.Logger.info("Plugin Version: %s", pluginVersionStr)

            HttpSessions.Init(TaggedLoggingAdapter(self.RawLogger, "HTTPSESSION"))

            Sentry.Setup(pluginVersionStr, "elegoo_cc2", devConfig is not None, enableProfiling=True, filterExceptionsByPackage=False, restartOnCantCreateThreadBug=True)

            self.Secrets = Secrets(TaggedLoggingAdapter(self.RawLogger, "SECRETS"), localStorageDir)
            self.DoFirstTimeSetupIfNeeded()

            printerId = self.GetPrinterId()
            privateKey = self.GetPrivateKey()
            if printerId is None or privateKey is None:
                raise Exception("Printer ID or Private Key is None! This should never happen, please report this issue to the OctoEverywhere team.")

            Sentry.SetPrinterId(printerId)

            DevLocalServerAddress_CanBeNone = self.GetDevConfigStr(devConfig, "LocalServerAddress")
            if DevLocalServerAddress_CanBeNone is not None:
                self.Logger.warning("~~~ Using Local Dev Server Address: %s ~~~", DevLocalServerAddress_CanBeNone)

            MDns.Init(TaggedLoggingAdapter(self.RawLogger, "MDNS"), localStorageDir)
            LocalWebApi.Init(TaggedLoggingAdapter(self.RawLogger, "LOCALWEBAPI"), printerId, self.Config)
            DeviceId.Init(TaggedLoggingAdapter(self.RawLogger, "DEVICEID"))
            PrintInfoManager.Init(TaggedLoggingAdapter(self.RawLogger, "PRINTINFO"), localStorageDir)

            configIpOrHostname = self.Config.GetStr(Config.SectionCompanion, Config.CompanionKeyIpOrHostname, None)
            if configIpOrHostname is not None:
                LocalIpHelper.SetLocalIpOverride(configIpOrHostname)

            stateTranslator = ElegooCc2StateTranslator(TaggedLoggingAdapter(self.RawLogger, "ELEGOOTRANSLATOR"))
            self.NotificationHandler = NotificationsHandler(self.Logger, stateTranslator)
            self.NotificationHandler.SetBedCooldownThresholdTemp(self.Config.GetFloatRequired(Config.GeneralSection, Config.GeneralBedCooldownThresholdTempC, Config.GeneralBedCooldownThresholdTempCDefault))
            stateTranslator.SetNotificationHandler(self.NotificationHandler)

            ElegooCc2FileManager.Init(TaggedLoggingAdapter(self.RawLogger, "FILEMANAGER"))
            ElegooCc2Client.Init(TaggedLoggingAdapter(self.RawLogger, "CLIENT"), self.Config, printerId, pluginVersionStr, stateTranslator, ElegooCc2FileManager.Get())

            # Init app storage so the NotificationSender can fetch push tokens from Firebase.
            AppStorageHelper.Init(TaggedLoggingAdapter(self.RawLogger, "APPS"), FirebaseAppStorage(TaggedLoggingAdapter(self.RawLogger, "DATABASE"), pluginVersionStr, ElegooCc2FirebaseIdentity(TaggedLoggingAdapter(self.RawLogger, "IDENTITY"))))

            # Now start the main runner! This blocks while the client maintains the printer connection.
            ElegooCc2Client.Get().RunBlocking()
        except Exception as e:
            Sentry.OnException("!! Exception thrown out of main Elegoo CC2 host run function.", e)

        try:
            self.Logger.info("##################################")
            self.Logger.info("######## OctoApp Exiting ##########")
            self.Logger.info("##################################")
            logging.shutdown()
        except Exception as e:
            print("Exception in logging.shutdown "+str(e))


    def DoFirstTimeSetupIfNeeded(self) -> None:
        printerId = self.GetPrinterId()
        if HostCommon.IsPrinterIdValid(printerId) is False:
            if printerId is None:
                self.Logger.info("No printer id was found, generating one now!")
            else:
                self.Logger.info("An invalid printer id was found [%s], regenerating!", str(printerId))
            printerId = HostCommon.GeneratePrinterId()
            self.Secrets.SetPrinterId(printerId)
            self.Logger.info("New printer id created: %s", printerId)

        privateKey = self.GetPrivateKey()
        if HostCommon.IsPrivateKeyValid(privateKey) is False:
            if privateKey is None:
                self.Logger.info("No private key was found, generating one now!")
            else:
                self.Logger.info("An invalid private key was found [%s], regenerating!", str(privateKey))
            privateKey = HostCommon.GeneratePrivateKey()
            self.Secrets.SetPrivateKey(privateKey)
            self.Logger.info("New private key created.")


    def GetPrinterId(self) -> Optional[str]:
        return self.Secrets.GetPrinterId()


    def GetPrivateKey(self) -> Optional[str]:
        return self.Secrets.GetPrivateKey()


    def GetDevConfigStr(self, devConfig:Optional[Dict[str, Any]], value:str) -> Optional[str]:
        if devConfig is None:
            return None
        if value in devConfig:
            v = devConfig[value]
            if v is not None and len(v) > 0 and v != "None":
                return v
        return None


    def Rekey(self, reason:str) -> None:
        self.Logger.error("HOST REKEY CALLED %s - Clearing keys...", reason)
        self.Secrets.SetPrinterId(None)
        self.Secrets.SetPrivateKey(None)
        self.Logger.error("Key clear complete, restarting plugin.")
        HostCommon.RestartPlugin()


    def ShowUiPopup(self, title:str, text:str, msgType:str, actionText:Optional[str], actionLink:Optional[str], showForSec:int, onlyShowIfLoadedViaOeBool:bool) -> None:
        ElegooCc2Client.Get().SendFrontendPopupMsg(title, text, msgType, actionText, actionLink, showForSec, onlyShowIfLoadedViaOeBool)


    def OnPrimaryConnectionEstablished(self, octoKey:str, connectedAccounts:List[str]) -> None:
        self.Logger.info("Primary Connection To OctoEverywhere Established - We Are Ready To Go!")
        LocalWebApi.Get().OnPrimaryConnectionEstablished(len(connectedAccounts) > 0)

        if len(connectedAccounts) == 0:
            printerId = self.GetPrinterId()
            if printerId is not None:
                LinkHelper.RunLinkPluginConsolePrinterAsync(self.Logger, printerId, "elegoo_cc2_host")


    def OnPluginUpdateRequired(self) -> None:
        self.Logger.error("!!! A Plugin Update Is Required -- If This Plugin Isn't Updated It Might Stop Working !!!")
        self.Logger.error("!!! Please SSH into the device running this plug-in and run the update script or update the docker container!  !!!")


    def OnRekeyRequired(self) -> None:
        self.Rekey("Handshake Failed")


    def OnRekeyCommand(self) -> bool:
        self.Rekey("Command")
        return True
