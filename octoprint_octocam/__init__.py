# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import octoprint.plugin
from octoprint.events import Events
import flask
import requests
import logging

class OctoCamPlugin(
		octoprint.plugin.AssetPlugin,
		octoprint.plugin.StartupPlugin,
		octoprint.plugin.TemplatePlugin,
		octoprint.plugin.SimpleApiPlugin,
		octoprint.plugin.SettingsPlugin,
		octoprint.plugin.EventHandlerPlugin,
		octoprint.plugin.RestartNeedingPlugin
	):


	def __init__(self):
		self._logger = logging.getLogger("octoprint.plugins.octocam")
		self.poll_status = None

	def get_settings_defaults(self):
		return dict(
			pollingEnabled=True,
			pollingInterval=15
		)

	def on_after_startup(self):
		self._logger.info("OctoCam loaded!")
		if self._settings.get(["pollingEnabled"]):
			self.poll_status = RepeatedTimer(int(self._settings.get(["pollingInterval"])) * 60, self.check_status)
			self.poll_status.start()

	def get_template_configs(self):
		return [
			dict(type="navbar", custom_bindings=True),
			dict(type="settings", custom_bindings=True)
		]

	def get_assets(self):
		# Define your plugin's asset files to automatically include in the
		# core UI here.
		return dict(
			js=["js/octocam.js"],
			css=["css/octocam.css"],
			#less=["less/octolight.less"]
		)

	def get_api_commands(self):
		return dict(
			turnOn=[],
			turnOff=[],
			checkStatus=[],
			toggle=[],
		)


	def on_after_startup(self):
		self._logger.info("OctoCam started, listening for request")
		response = self.check_status()
		self._plugin_manager.send_plugin_message(self._identifier, response)
		self._logger.info("OctoCam light has status: %s" % response)

	def on_api_command(self, command, data):
		if command == 'turnOn':
			response = self.turn_on_off(True)
		elif command == 'turnOff':
			response = self.turn_on_off(False)
		elif command == 'toggle':
			response = self.toggle()
		elif command == 'checkStatus':
			response = self.check_status()

		return flask.jsonify(response)

	def get_octocam_url(self):
		address = self._settings.global_get(["webcam", "stream"]).strip("/")
		url = address + "/torch"
		return url

	def toggle(self):
		response = self.check_status()
		return self.turn_on_off(not response['torchOn'])

	def turn_on_off(self, on):
		url = self.get_octocam_url()
		self._logger.info("Updating status to %s" % on)
		r = requests.post(url, timeout=float(5), json={'torchOn': on})
		if r.status_code != requests.codes.ok:
			self._logger.info("Failed to update status for %s" % url)
			raise Error("Unable to update status")
		response = r.json()
		self._logger.info("OctoCam light has status: %s" % response)
		self._plugin_manager.send_plugin_message(self._identifier, response)
		return response

	def check_status(self):
		url = self.get_octocam_url()
		r = requests.get(url, timeout=float(5))
		if r.status_code != requests.codes.ok:
			self._logger.info("Failed to request status from %s, recevied %d" % url, r.status_code)
			raise Exception("Unable to request status, received %d" ^ r.status_code)
		response = r.json()
		self._logger.info("OctoCam light has status: %s" % response)
		self._plugin_manager.send_plugin_message(self._identifier, response)
		return response

	def on_event(self, event, payload):
		if event == Events.CLIENT_OPENED:
			self.check_status()
			return

	def get_update_information(self):
		return dict(
			octolight=dict(
				displayName="OctoCam",
				displayVersion=self._plugin_version,

				type="github_release",
				current=self._plugin_version,

				user="crysxd",
				repo="OctoCam",
				pip="https://github.com/crysxd/OctoCam-Plugin/archive/{target}.zip"
			)
		)

__plugin_pythoncompat__ = ">=2.7,<4"
__plugin_implementation__ = OctoCamPlugin()

__plugin_hooks__ = {
	"octoprint.plugin.softwareupdate.check_config":
	__plugin_implementation__.get_update_information
}
