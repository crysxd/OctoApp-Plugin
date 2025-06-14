from typing import Dict, Any, Optional
from io import RawIOBase

import octoprint.filemanager
import octoprint.filemanager.util

from octoapp.logging import LoggerLike
from octoapp.sentry import Sentry
from octoapp.layerutils import LayerUtils

class LayerProcessor(octoprint.filemanager.util.LineProcessorStream):
    staticLogger: Optional[LoggerLike] = None

    def __init__(self, logger:LoggerLike, input_stream:RawIOBase):
        super().__init__(input_stream)
        self.LayerCounter = 0
        self.FirstLine = True
        self.Disabled = False
        self.__class__.staticLogger = logger
        self.Context:Dict[str,Any] = {}

    def process_line(self, line:bytes):
        try:
            decodedLine = line.decode()

            if decodedLine.replace('\n', '').replace('\r', '') in LayerUtils.DisableLegacyLayerCommands:
                self.Disabled = True

            if self.Disabled is True:
                return line

            if LayerUtils.IsLayerChange(decodedLine, self.Context):
                result = (decodedLine + LayerUtils.CreateLayerChangeCommands(self.LayerCounter)[0] + "\r\n").encode()
                self.LayerCounter += 1
                return result

            if self.FirstLine:
                self.FirstLine = False
                return (LayerUtils.DisableLegacyLayerCommands[0] + "\r\n" + decodedLine).encode()

            return line
        except Exception as e:
            Sentry.ExceptionNoSend("Failed to process", e)
            raise e

    @staticmethod
    def InsertLayerChanges(path:str, file_object: Any, links:Optional[Any]=None, printer_profile:Optional[Any]=None, allow_overwrite:bool=True, *args:Any, **kwargs:Any) -> octoprint.filemanager.util.StreamWrapper:
        if not octoprint.filemanager.valid_file_type(path, type="gcode"):  # type: ignore
            return file_object

        if LayerProcessor.staticLogger is not None:
            LayerProcessor.staticLogger.info("Processing " + path)

        return octoprint.filemanager.util.StreamWrapper(file_object.filename, LayerProcessor(file_object.stream()))  # type: ignore
