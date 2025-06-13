from typing import Dict, Any, Optional

from .subplugin import OctoAppSubPlugin

from octoprint.access.permissions import Permissions
from octoapp.sentry import Sentry
import flask

class OctoAppPrinterFirmwareSubPlugin(OctoAppSubPlugin):

    def __init__(self, parent):
        super().__init__(parent)
        self.firmware_info:Dict[str,Any] = {}


    def OnFirmwareInfoReceived(self, comm_instance:Any, firmware_name:str, firmware_data:Dict[str,Any], *args:Any, **kwargs:Any):
        Sentry.Debug("FIRMWARE", "Received firmware info")
        self.firmware_info = firmware_data


    def OnApiCommand(self, command:str, data: Dict[str,Any]) -> Optional[flask.Response]:
        if command == "getPrinterFirmware":
            if not Permissions.PLUGIN_OCTOAPP_GET_DATA.can(): # type: ignore
                return flask.make_response("Insufficient rights", 403)
            return flask.jsonify(self.firmware_info)
        else: 
            return None