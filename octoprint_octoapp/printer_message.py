
from .sub_plugin import OctoAppSubPlugin

from octoprint.events import Events

class OctoAppPrinterMessageSubPlugin(OctoAppSubPlugin):


    def __init__(self, parent):
        super().__init__(parent)


    def on_gcode_send(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        if gcode == "M117":
            message = cmd.split(' ', 1)[1]
            self.parent.plugin_state["m117"] = message
            self._logger.debug("MESSAGE      | M117 message changed: %s" % message)
            self.parent.send_plugin_state_message()
            return

    
    def on_event(self, event, payload):    
        if event == Events.PRINT_STARTED or event == Events.PRINT_DONE or event == Events.PRINT_FAILED or event == Events.PRINT_CANCELLED:
            self.parent.plugin_state["m117"] = None
            self.parent.send_plugin_state_message()
