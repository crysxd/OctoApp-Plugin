# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import octoprint.plugin
import flask

import RPi.GPIO as GPIO

GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)

class OctoLightPlugin(
		octoprint.plugin.StartupPlugin,
		octoprint.plugin.TemplatePlugin,
		octoprint.plugin.SimpleApiPlugin,
		octoprint.plugin.SettingsPlugin
	):

	light_state = False

	def get_settings_defaults(self):
		return dict(
			light_pin = 13
		)
	
	def get_template_configs(self):
		return [
			dict(type="navbar", custom_bindings=False),
			dict(type="settings", custom_bindings=False)
		]

	def on_after_startup(self):
		self.light_state = False
		self._logger.info("OctoLight started, listening for GET request")
		self._logger.info(self._settings.get(["light_pin"]))
	
	def on_api_get(self, request):
		GPIO.setup(int(self._settings.get(["light_pin"])), GPIO.OUT)
		self.light_state = not self.light_state
		self._logger.info(self._settings.get(["light_pin"]))
		if self.light_state:
			GPIO.output(int(self._settings.get(["light_pin"])), GPIO.HIGH)
		else:
			GPIO.output(int(self._settings.get(["light_pin"])), GPIO.LOW)

		self._logger.info("Got request. Light state: %s" % self.light_state)

		return flask.jsonify(status="ok")

__plugin_pythoncompat__ = ">=2.7,<4"
__plugin_implementation__ = OctoLightPlugin()