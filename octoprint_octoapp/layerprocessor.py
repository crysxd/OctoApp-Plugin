from .subplugin import OctoAppSubPlugin
from octoapp.notificationshandler import NotificationsHandler
from octoapp.sentry import Sentry
import octoprint.plugin
import octoprint.filemanager
import octoprint.filemanager.util
from octoapp.layerutils import LayerUtils

from octoprint.util.comm import strip_comment

class LayerProcessor(octoprint.filemanager.util.LineProcessorStream):

    def __init__(self, input_stream):
        super().__init__(input_stream)
        self.LayerCounter = 0
        self.FirstLine = True
        self.Context = {}

    def process_line(self, line):
        try:
            decodedLine = line.decode()

            if decodedLine.startswith(LayerUtils.LayerChangeCommand) or decodedLine.startswith(LayerUtils.DisableLegacyLayerCommand):
                return None
            
            if LayerUtils.IsLayerChange(decodedLine, self.Context):
                result = (decodedLine + LayerUtils.CreateLayerChangeCommand(self.LayerCounter) + "\r\n").encode()
                self.LayerCounter += 1
                return result
            
            if self.FirstLine:
                self.FirstLine = False
                return (LayerUtils.DisableLegacyLayerCommand + "\r\n" + decodedLine).encode()
            
            return line
        except Exception as e:
            Sentry.ExceptionNoSend("Failed to process", e)
            raise e
    
    @staticmethod
    def InsertLayerChanges(path, file_object, links=None, printer_profile=None, allow_overwrite=True, *args, **kwargs):
        if not octoprint.filemanager.valid_file_type(path, type="gcode"):
            return file_object

        Sentry.Info("Layers", "Processing " + path)

        
        return octoprint.filemanager.util.StreamWrapper(file_object.filename, LayerProcessor(file_object.stream()))

