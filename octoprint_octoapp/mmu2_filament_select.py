
from .sub_plugin import OctoAppSubPlugin
from .notifications import OctoAppNotificationsSubPlugin

class OctoAppMmu2FilamentSelectSubPlugin(OctoAppSubPlugin):


    def __init__(self, parent, notification_plugin: OctoAppNotificationsSubPlugin):
        super().__init__(parent)
        self.notifications = notification_plugin


    def on_emit_websocket_message(self, user, message, type, data):
        if type == "plugin" and data.get("plugin") == "mmu2filamentselect" and isinstance(data.get("data"), dict):
            action = data.get("data").get("action")

            if action == "show":
                # If not currently active, send notification as we switched state
                if self.parent.plugin_state.get("mmuSelectionActive") is not True:
                    self.notifications.send_notification(event=self.notifications.EVENT_MMU2_FILAMENT_START)

                self.parent.plugin_state["mmuSelectionActive"] = True
                self.parent.send_plugin_state_message()

            elif action == "close":
                # If currently active, send notification as we switched state
                if self.parent.plugin_state.get("mmuSelectionActive") is True:
                    self.notifications.send_notification(event=self.notifications.EVENT_MMU2_FILAMENT_DONE)

                self.parent.plugin_state["mmuSelectionActive"] = False
                self.parent.send_plugin_state_message()