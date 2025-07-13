from typing import Dict, Any, Optional

import flask

from octoprint.access.permissions import Permissions
from octoapp.logging import LoggerLike

from .subplugin import IOctoAppSubPluginParent, OctoAppSubPlugin


class OctoAppPrinterFirmwareSubPlugin(OctoAppSubPlugin):

    def __init__(self, logger:LoggerLike, parent: IOctoAppSubPluginParent):
        super().__init__(logger, parent)
        self.firmware_info:Dict[str,Any] = {}


    def OnFirmwareInfoReceived(self, comm_instance:Any, firmware_name:str, firmware_data:Dict[str,Any], *args:Any, **kwargs:Any):
        self.logger.info("Received firmware info")
        self.firmware_info = firmware_data


    def OnApiCommand(self, command:str, data: Dict[str,Any]) -> Optional[flask.Response]:
        if command == "getPrinterFirmware":
            if not Permissions.PLUGIN_OCTOAPP_GET_DATA.can(): # type: ignore
                return flask.make_response("Insufficient rights", 403)
            return flask.jsonify(self.firmware_info)
        else:
            return None
