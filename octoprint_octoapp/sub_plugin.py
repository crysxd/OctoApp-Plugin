class OctoAppSubPlugin():


    def __init__(self, parent):
        self.config = dict()
        self.parent = parent
        self._logger = parent._logger


    def on_after_startup(self):
        pass


    def on_firmware_info_received(self, comm_instance, firmware_name, firmware_data, *args, **kwargs):
        pass


    def on_api_command(self, command, data):
        return None


    def on_emit_websocket_message(self, user, message, type, data):
        pass


    def on_print_progress(self, storage, path, progress):
        pass


    def on_event(self, event, payload):
        pass


    def on_gcode_send(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
        pass