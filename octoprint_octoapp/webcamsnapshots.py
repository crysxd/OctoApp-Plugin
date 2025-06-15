from typing import Dict, Any, Optional

import flask

from .subplugin import OctoAppSubPlugin

class OctoAppWebcamSnapshotsSubPlugin(OctoAppSubPlugin):

    def OnApiCommand(self, command: str, data:Dict[str,Any]) -> Optional[flask.Response]:
        if command == "getWebcamSnapshot":
            return flask.make_response("Insufficient rights", 403)
        else:
            return None
