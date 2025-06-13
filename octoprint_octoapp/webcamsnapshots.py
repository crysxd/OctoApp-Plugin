
from .subplugin import OctoAppSubPlugin

import threading
import flask
from flask import send_file
from . import OctoAppPlugin

class OctoAppWebcamSnapshotsSubPlugin(OctoAppSubPlugin):

    def __init__(self, parent: OctoAppPlugin):
        super().__init__(parent)
    
    def OnApiCommand(self, command, data):
        if command == "getWebcamSnapshot":
                return flask.make_response("Insufficient rights", 403)
        else:
            return None