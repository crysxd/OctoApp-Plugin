
import flask

from . import OctoAppSubPlugin

class OctoAppWebcamSnapshotsSubPlugin(OctoAppSubPlugin):

    def OnApiCommand(self, command, data):
        if command == "getWebcamSnapshot":
            return flask.make_response("Insufficient rights", 403)
        else:
            return None
