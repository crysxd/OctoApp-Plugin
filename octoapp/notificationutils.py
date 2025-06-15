from typing import IO, Dict, Any

from .sentry import Sentry
from .layerutils import LayerUtils
from .logging import LoggerLike
from .notificationshandler import NotificationsHandler

class NotificationUtils:

    NotificationCommand = "OCTOAPP_NOTIFY"
    FirstLayerCompletedAt = "FirstLayerCompletedAt"
    ThirdLayerCompletedAt = "ThirdLayerCompletedAt"

    _Instance:"NotificationUtils" = None #pyright: ignore[reportAssignmentType]

    @staticmethod
    def Init(logger:LoggerLike):
        NotificationUtils._Instance = NotificationUtils(logger)


    @staticmethod
    def Get():
        return NotificationUtils._Instance

    def __init__(self, logger:LoggerLike) -> None:
        self.Logger = logger

    def CreateNotificationCommand(self, message:str):
        return NotificationUtils.NotificationCommand + " MESSAGE=" + message


    def GetMessageIfNotifyCommand(self, line:str):
        def removeQuotes(s:str):
            if s.startswith('"') and s.endswith('"'):
                return s[1:-1]
            elif s.startswith("'") and s.endswith("'"):
                return s[1:-1]
            return s

        base = self.CreateNotificationCommand("")
        commands = [ base, ";" + base, "; " + base, "M118 E1 " + base]

        for command in commands:
            if line.startswith(command):
                return removeQuotes(line[len(command):])

    def SendScheduledNotifications(self, notifications: Dict[int, str], notificationHandler: NotificationsHandler, filePos:int, lastFilePos:int):
        for notificationFilePos in notifications:
            if notificationFilePos > lastFilePos and filePos >= notificationFilePos:
                message = notifications[notificationFilePos]
                self.Logger.info( f"Sending scheduled notification at {filePos}: {message}")
                if message == NotificationUtils.FirstLayerCompletedAt:
                    notificationHandler.OnFirstLayerDone()
                elif message == NotificationUtils.ThirdLayerCompletedAt:
                    notificationHandler.OnThirdLayerDone()
                else:
                    notificationHandler.OnCustomNotification(message)

    def ExtractNotifications(self, response:IO[Any], stopAfterLayer3:bool = False) -> Dict[int, str]:
        buffer = ""
        filePos = 0
        context:Dict[str,Any] = {}
        notifications:Dict[int, str] = {}

        def processLine(line:str) -> bool:
            context['layerCounter'] = context.get('layerCounter', 0)

            try:
                if LayerUtils.IsLayerChange(line, context):
                    if context['layerCounter'] <= 4:
                        self.Logger.info( "Layer " + str(context['layerCounter']) + " completed at at " + str(filePos))

                    if context['layerCounter'] == 1:
                        notifications[filePos] = NotificationUtils.FirstLayerCompletedAt

                    if context['layerCounter'] == 3:
                        notifications[filePos] = NotificationUtils.ThirdLayerCompletedAt
                        if stopAfterLayer3:
                            return False

                    context['layerCounter'] += 1

                notifyMessage = self.GetMessageIfNotifyCommand(line)
                if notifyMessage is not None:
                    self.Logger.info( "Custom notification at " + str(filePos))
                    notifications[filePos] = notifyMessage

            except Exception as e:
                Sentry.ExceptionNoSend("Failed to detect layer change", e)

            return True

        # Do not read by line! We need to keep track of \r and \n because they are part of the filePos
        # later used. If read by line we do not know if \r\n or \n was used
        line = ""
        while True:
            chunk: Any = response.read(4096)
            if not chunk:
                break


            buffer += chunk if isinstance(chunk, str) else chunk.decode('utf-8')
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                filePos += len(line) + 1 # +1 for \n
                if processLine(line.strip()) is False:
                    self.Logger.info( "Processing stopped prematurely, all notifications extracted")
                    return notifications

        if buffer and len(line) > 0:
            processLine(line)

        return notifications
