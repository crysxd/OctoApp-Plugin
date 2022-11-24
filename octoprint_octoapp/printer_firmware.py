
from .sub_plugin import OctoAppSubPlugin

from octoprint.access.permissions import Permissions
import flask

class OctoAppPrinterFirmwareSubPlugin(OctoAppSubPlugin):


    def __init__(self, parent):
        super().__init__(parent)
        self.firmware_info = {}


    def on_firmware_info_received(
        self, comm_instance, firmware_name, firmware_data, *args, **kwargs
    ):
        self._logger.debug("Recevied firmware info")
        self.firmware_info = firmware_data


    def on_api_command(self, command, data):
        if command == "getPrinterFirmware":
            if not Permissions.PLUGIN_OCTOAPP_GET_DATA.can():
                return flask.make_response("Insufficient rights", 403)
            return flask.jsonify(self.firmware_info)
        else: 
            return None