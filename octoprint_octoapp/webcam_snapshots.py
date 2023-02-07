
from .sub_plugin import OctoAppSubPlugin

import threading
import time
from PIL import Image
import requests
from io import BytesIO
import flask
from datetime import datetime
from octoprint.access.permissions import Permissions

from flask import send_file

class OctoAppWebcamSnapshotsSubPlugin(OctoAppSubPlugin):


    def __init__(self, parent):
        super().__init__(parent)
        self.webcam_snapshot_cache = {}
        self.webcam_snapshot_cache_lock = threading.Lock()


    def on_after_startup(self):
        self.continuous_snapshot_update()

    
    def on_api_command(self, command, data):
        if command == "getWebcamSnapshot":
            if not Permissions.PLUGIN_OCTOAPP_GET_DATA.can():
                return flask.make_response("Insufficient rights", 403)

            try:
                with self.webcam_snapshot_cache_lock:
                    webcamIndex = data.get("webcamIndex", 0)
                    webcamSettings = self.get_webcam_settings(webcamIndex)
                    cache = self.webcam_snapshot_cache.get(webcamIndex)
                    
                    if (cache == None):
                        return flask.make_response("Webcam image for {} not cached".format(webcamIndex), 406)
                    
                    secondsSince = (datetime.now() - cache.get("time")).total_seconds()
                    if (secondsSince > 60):
                         return flask.make_response("Webcam image for {} outdated".format(webcamIndex), 406)
                
                    image = Image.open(cache.get("bytes")).copy()
                    size = min(max(image.width, image.height), int(data.get("size", 720)))
                    image.thumbnail([size, size])

                    if (webcamSettings.get("rotate90")):
                        image = image.rotate(90, expand=True)

                    if (webcamSettings.get("flipV")):
                        image = image.transpose(Image.FLIP_TOP_BOTTOM)

                    if (webcamSettings.get("flipH")):
                        image = image.transpose(Image.FLIP_LEFT_RIGHT)

                    imageBytes = BytesIO()
                    image.save(imageBytes, 'JPEG', quality=data.get("quality", 50))
                    imageBytes.seek(0)
                    return send_file(imageBytes, mimetype='image/jpeg')
            except Exception as e:
                self._logger.warning("Failed to get webcam snapshot %s" % e)
                self._logger.exception(e)
                return flask.make_response("Failed to get snapshot from webcam", 500)
        
        else:
            return None


    #
    # SNAPSHOTS
    #


    def continuous_snapshot_update(self):
        t = threading.Thread(
            target=self.do_continuous_snapshot_update,
            args=[]
        )
        t.daemon = True
        t.start()


    def do_continuous_snapshot_update(self):
        while True:
            multiCamSettings = webcamSettings = self.parent._settings.global_get(
                ["plugins", "multicam", "multicam_profiles"]
            )

            if (type(multiCamSettings) == list):
                for i in range(len(multiCamSettings) - 1):
                    self.update_snapshot_cache(i)
            else:
                self.update_snapshot_cache(0) 

            time.sleep(5)


    def update_snapshot_cache(self, webcamIndex):
        try:
            webcamSettings = self.get_webcam_settings(webcamIndex)
            snapshotUrl = webcamSettings["snapshot"]
            timeout = self.parent._settings.global_get_int(
                ["webcam", "snapshotTimeout"]
            )
            self._logger.debug(
                "Getting snapshot from {0} (index {1}, {2})".format(
                    snapshotUrl, webcamIndex, webcamSettings)
            )
            imageBytes = BytesIO()
            raw = requests.get(
                snapshotUrl, timeout=float(timeout), stream=True)

            for chunk in raw.iter_content(chunk_size=128):
                imageBytes.write(chunk)

            with self.webcam_snapshot_cache_lock:
                self.webcam_snapshot_cache[webcamIndex] = dict(bytes=imageBytes, time=datetime.now())
        except Exception as e:
            self._logger.warning("Failed to get webcam snapshot %s" % e)
      
           
    def get_webcam_settings(self, webcamIndex):
        if (webcamIndex == 0):
            return self.parent._settings.global_get(["webcam"])
        else:
            return self.parent._settings.global_get(
                ["plugins", "multicam", "multicam_profiles"]
            )[webcamIndex]