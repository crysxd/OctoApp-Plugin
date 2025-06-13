from typing import Dict, Any

from .subplugin import OctoAppSubPlugin
from octoapp.notificationshandler import NotificationsHandler
from octoapp.sentry import Sentry
from octoapp.notificationsender import NotificationSender
from . import OctoAppPlugin

class OctoAppMmu2FilamentSelectSubPlugin(OctoAppSubPlugin):


    def __init__(self, parent:OctoAppPlugin, notification_handler: NotificationsHandler):
        super().__init__(parent)
        self.NotificationsHandler = notification_handler


    def OnEmitWebsocketMessage(self, user:str, message:str, type, data:Dict[str,Any]):
        if type == "plugin" and data.get("plugin") in ["mmu2filamentselect", "prusammu"] and isinstance(data.get("data"), dict):
            action = data.get("data", {}).get("action", None)

            Sentry.Info("MMU", "Received event: %s" % action)

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