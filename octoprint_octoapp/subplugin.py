from typing import Any,Dict,Optional

from flask import Response

from . import OctoAppPlugin

class OctoAppSubPlugin():


    def __init__(self, parent:OctoAppPlugin):
        self.config:Dict[str,Any] = {}
        self.parent = parent
        self._logger = parent._logger


    def OnAfterStartup(self):
        pass

    def OnFirmwareInfoReceived(self, comm_instance:Any, firmware_name:str, firmware_data:Dict[str,Any], *args:Any, **kwargs:Any):
        pass


    def OnApiCommand(self, command:str, data:Dict[str,Any]) -> Optional[Response]:
        return None


    def OnEmitWebsocketMessage(self, user:str, message:str, type:str, data:Dict[str,Any]):
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