from abc import abstractmethod
from typing import Optional, Dict, Any
from logging import Logger

import octoprint.plugin
import octoprint.printer
import flask

from octoapp.logging import LoggerLike


class IOctoAppSubPluginParent(
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.ProgressPlugin,
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.SimpleApiPlugin,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.EventHandlerPlugin,
    octoprint.plugin.RestartNeedingPlugin,
    octoprint.printer.PrinterCallback
):

    @property
    @abstractmethod
    def PluginState(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def SendPluginStateMessage(self):
        pass


class OctoAppSubPlugin():

    def __init__(self, logger: LoggerLike, parent: IOctoAppSubPluginParent):
        self.config:Dict[str,Any] = {}
        self.logger = logger
        self.parent = parent

    def OnAfterStartup(self):
        pass

    def OnFirmwareInfoReceived(self, comm_instance:Any, firmware_name:str, firmware_data:Dict[str,Any], *args:Any, **kwargs:Any):
        pass


    def OnApiCommand(self, command:str, data:Dict[str,Any]) -> Optional[flask.Response]:
        return None


    def OnEmitWebsocketMessage(self, user:str, message:str, messageType:str, data:Dict[str,Any]):
        pass


    def OnPrintProgress(self, storage:str, path:str, progress:int):
        pass


    def OnEvent(self, event:str, payload:Dict[str,Any]):
        pass


    def OnGcodeSent(self, comm_instance:Any, phase:Any, cmd:str, cmd_type:str, gcode:str, *args:Any, **kwargs:Any):
        pass


    def OnGcodeQueued(self, comm_instance:Any, phase:Any, cmd:str, cmd_type:str, gcode:str, *args:Any, **kwargs:Any) -> bool:
        return True


    def OnGcodeReceived(self, comm_instance:Any, line:str, *args:Any, **kwargs:Any):
        pass


    def OnCurrentData(self, data:Dict[str,Any]):
        pass
