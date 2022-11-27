# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

from .plugin import OctoAppPlugin

__plugin_pythoncompat__ = ">=3,<4"
__plugin_implementation__ = OctoAppPlugin()

__plugin_hooks__ = {
    "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
    "octoprint.comm.protocol.firmware.info": __plugin_implementation__.on_firmware_info_received,
    "octoprint.comm.protocol.gcode.queuing": __plugin_implementation__.on_gcode_send,
    "octoprint.access.permissions": __plugin_implementation__.get_additional_permissions,
    "octoprint.server.sockjs.emit": __plugin_implementation__.on_emit_websocket_message,
}

