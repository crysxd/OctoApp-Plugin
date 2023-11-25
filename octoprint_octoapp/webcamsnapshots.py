
from .subplugin import OctoAppSubPlugin

import threading
import time
from PIL import Image
import requests
from io import BytesIO
import flask
from datetime import datetime
from octoprint.access.permissions import Permissions
from octoapp.sentry import Sentry
from flask import send_file

class OctoAppWebcamSnapshotsSubPlugin(OctoAppSubPlugin):


    def __init__(self, parent):
        super().__init__(parent)
        self.webcam_snapshot_cache = {}
        self.webcam_snapshot_cache_lock = threading.Lock()


    def OnAfterStartup(self):
        self._continuouslyUpdateSnapshots()

    
    def OnApiCommand(self, command, data):
        if command == "getWebcamSnapshot":
            if not Permissions.PLUGIN_OCTOAPP_GET_DATA.can():
                return flask.make_response("Insufficient rights", 403)

            try:
                with self.webcam_snapshot_cache_lock:
                    webcamIndex = data.get("webcamIndex", 0)
                    webcamSettings = self._getWebcamSettings(webcamIndex)
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
                Sentry.ExceptionNoSend("Failed to get webcam snapshot", e)
                return flask.make_response("Failed to process snapshot from webcam", 500)
        
        else:
            return None


    #
    # SNAPSHOTS
    #


    def _continuouslyUpdateSnapshots(self):
        t = threading.Thread(
            target=self._doContinuouslyUpdateSnapshots,
            args=[]
        )
        t.daemon = True
        t.start()


    def _doContinuouslyUpdateSnapshots(self):
        failure_count = 0
        success_count = 0
        
        while True:
            multiCamSettings = webcamSettings = self.parent._settings.global_get(
                ["plugins", "multicam", "multicam_profiles"]
            )

            success = True
            log = success_count % 10 == 0

            if (type(multiCamSettings) == list):
                for i in range(len(multiCamSettings)):
                   success = success and self._updateSnapshotCache(webcamIndex = i, log = log)
            else:
                success = success and self._updateSnapshotCache(webcamIndex = 0, log = log) 

            if success is False:
                failure_count += 1
                success_count = 0
            else:
                failure_count = 0
                success_count += 1

            time.sleep(min(120, (failure_count + 1) * 5))


    def _updateSnapshotCache(self, webcamIndex, log):
        try:
            webcamSettings = self._getWebcamSettings(webcamIndex)
            snapshotUrl = webcamSettings["snapshot"]

            if snapshotUrl == "" or snapshotUrl is None:
                return True

            timeout = self.parent._settings.global_get_int(
                ["webcam", "snapshotTimeout"]
            )
            if log:
                Sentry.Debug("SNAPSHOT", "Failed to get webcam snapshot", e)
            imageBytes = BytesIO()
            raw = requests.get(
                snapshotUrl, timeout=float(timeout), stream=True)

            for chunk in raw.iter_content(chunk_size=128):
                imageBytes.write(chunk)

            with self.webcam_snapshot_cache_lock:
                self.webcam_snapshot_cache[webcamIndex] = dict(bytes=imageBytes, time=datetime.now())

            return True
        except Exception as e:
            Sentry.ExceptionNoSend("Failed to get webcam snapshot", e)
            return False
      
           
    def _getWebcamSettings(self, webcamIndex):
        if (webcamIndex == 0):
            return self.parent._settings.global_get(["webcam"])
        else:
            return self.parent._settings.global_get(
                ["plugins", "multicam", "multicam_profiles"]
            )[webcamIndex]