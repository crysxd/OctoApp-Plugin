from typing import Dict, Any
from logging import Logger

from octoapp.notificationshandler import NotificationsHandler
from octoapp.sentry import Sentry
from octoapp.notificationsender import NotificationSender
from .subplugin import IOctoAppSubPluginParent, OctoAppSubPlugin

class OctoAppMmu2FilamentSelectSubPlugin(OctoAppSubPlugin):


    def __init__(self, logger: Logger, parent:IOctoAppSubPluginParent, notificationHandler: NotificationsHandler):
        super().__init__(logger, parent)
        self.NotificationsHandler = notificationHandler


    def OnEmitWebsocketMessage(self, user:str, message:str, messageType:str, data:Dict[str,Any]):
        if type == "plugin" and data.get("plugin") in ["mmu2filamentselect", "prusammu"] and isinstance(data.get("data"), dict):
            action = data.get("data", {}).get("action", None)

            Sentry.Info("MMU", f"Received event: {action}")

            if action == "show":
                # If not currently active, send notification as we switched state
                if self.parent.PluginState.get("mmuSelectionActive") is not True:
                    Sentry.Info("MMU", "Trigger shown")
                    self.NotificationsHandler.NotificationSender.SendNotification(event=NotificationSender.EVENT_MMU2_FILAMENT_START)

                self.parent.PluginState["mmuSelectionActive"] = True
                self.parent.SendPluginStateMessage()

            elif action == "close":
                # If currently active, send notification as we switched state
                if self.parent.PluginState.get("mmuSelectionActive") is True:
                    Sentry.Info("MMU", "Trigger closed")
                    self.NotificationsHandler.NotificationSender.SendNotification(event=NotificationSender.EVENT_MMU2_FILAMENT_DONE)

                self.parent.PluginState["mmuSelectionActive"] = False
                self.parent.SendPluginStateMessage()
