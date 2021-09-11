# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import octoprint.plugin
from octoprint.events import Events
import flask
import requests
import logging
import time
import json
import threading

class OctoAppPlugin(
		octoprint.plugin.AssetPlugin,
		octoprint.plugin.ProgressPlugin,
		octoprint.plugin.StartupPlugin,
		octoprint.plugin.TemplatePlugin,
		octoprint.plugin.SimpleApiPlugin,
		octoprint.plugin.SettingsPlugin,
		octoprint.plugin.EventHandlerPlugin,
		octoprint.plugin.RestartNeedingPlugin,
	):


	def __init__(self):
		self._logger = logging.getLogger("octoprint.plugins.octoapp")

		self.default_config = dict(
			maxUpdateIntervalSecs=60,
			sendNotificationUrl="https://europe-west1-octoapp-4e438.cloudfunctions.net/sendNotification"
		)
		self.cached_config = self.default_config
		self.cached_config_at = 0

		self.last_progress_notification_at = 0
		self.last_progress = None
		self.last_print_name = None

	def get_settings_defaults(self):
		return dict(
			registeredApps=[],
		)

	def on_after_startup(self):
		self._logger.info("OctoApp started, updating config")
		self.get_config()

	def get_template_configs(self):
		return [
			dict(type="settings", custom_bindings=True)
		]

	def get_api_commands(self):
		return dict(
			registerForNoitifications=[],
		)

	def on_print_progress(self, storage, path, progress):
		self.last_progress = progress
		self.last_time_left = self._printer.get_current_data()["progress"]["printTimeLeft"]

		# check if we are allowed to send an update already
		config = self.get_config()
		earliest_update_at = self.last_progress_notification_at + config['maxUpdateIntervalSecs']
		if time.time() < earliest_update_at:
			self._logger.debug("Skipping update, next update at %s" % earliest_update_at)
			return

		# send update
		self.send_notification(
		   dict(
			   type='printing',
			   fileName=self.last_print_name,
			   progress=self.last_progress,
			   timeLeft=self.last_time_left
		   )
		)

	def on_event(self, event, payload):
		self._logger.debug("Recevied event %s" % event)
		if event == 'PrintStarted':
			self.last_print_name = payload['name']
			self.last_progress_notification_at = 0

		if event == 'PrintDone':
			self.last_progress = None
			self.last_print_name = None
			self.last_time_left = None
			self.send_notification(
				dict(
					type='completed',
					fileName=payload['name']
				)
			)

		elif event == 'PrintFailed' or event == 'PrintCancelled':
			self.last_progress = None
			self.last_print_name = None
			self.send_notification(
				dict(
					type='idle',
					fileName=payload['name']
				)
			)

		elif event == 'FilamentChange' and self.last_progress != None:
			self.send_notification(
				dict(
					type='filament_required',
					fileName=self.last_print_name,
					progress=self.last_progress,
					timeLeft=self.last_time_left,
				)
			 )

		elif event == 'PrintPaused':
			self.send_notification(
				dict(
					type='paused',
					fileName=payload['name'],
					progress=self.last_progress,
					timeLeft=self.last_time_left,
				)
			)

	def send_notification(self, data):
		self._logger.debug('send_notification')
		threading.Thread(target=self.do_send_notification, args=[data]).start()

	def do_send_notification(self, data):
		self._logger.debug('do_send_notification')
		config = self.get_config()
		jsonData = json.dumps(data)
		body=dict(
			targets=self._settings.get(['registeredApps']),
			data=jsonData
		)
		self._logger.debug('Sending notification %s' % body)

		r = requests.post(
			config['sendNotificationUrl'],
			timeout=float(15),
			json=body
		)
		if r.status_code != requests.codes.ok:
			raise Exception('Unexpected response code %d' % r.status_code)


	def on_api_command(self, command, data):
		self._logger.info("Recevied command %s" % command)

		if command == 'registerForNoitifications':
			instanceId = data['instanceId']
			fcmToken = data['fcmToken']
			displayName = data['displayName']

			# load apps and filter the given FCM token out
			apps = self._settings.get(['registeredApps'])
			apps = [app for app in apps if app['fcmToken'] != fcmToken]

			# add app for new registration
			apps.append(
				dict(
					fcmToken=fcmToken,
					instanceId=instanceId,
					displayName=displayName,
					registeredAt=time.time()
				)
			)

			# save
			self._logger.info("Registered app %s" % displayName)
			self._logger.debug("registeredApps %s" % apps)
			self._settings.set(['registeredApps'], apps)
			self._settings.save()

		return flask.jsonify(dict())

	def get_config(self):
		threading.Thread(target=self.do_update_config).start()
		return self.cached_config

	def do_update_config(self):
		# If we have no config cached or the cache is older than a day, request new config
		cache_config_max_age = time.time() - 86400
		if (self.cached_config != None) and (self.cached_config_at > cache_config_max_age):
			self._logger.debug("Reusing cached config")
			return self.cached_config

		# Request config, fall back to default
		try:
			r = requests.get("https://www.octoapp.eu/pluginconfig.json", timeout=float(15))
			if r.status_code != requests.codes.ok:
				raise Exception('Unexpected response code %d' % r.status_code)
			self.cached_config = r.json()
			self.cached_config_at = time.time()
			self._logger.info("OctoApp loaded config: %s" % self.cached_config)
		except Exception as e:
			self._logger.info("Failed to fetch config using defaults for 5 minutes, recevied %s" % e)
			self.cached_config = self.default_config
			self._logger.info("OctoApp loaded config: %s" % self.cached_config)
			self.cached_config_at = cache_config_max_age + 300

	def get_update_information(self):
		return dict(
			octolight=dict(
				displayName="OctoApp",
				displayVersion=self._plugin_version,

				type="github_release",
				current=self._plugin_version,

				user="crysxd",
				repo="OctoApp",
				pip="https://github.com/crysxd/OctoApp-Plugin/archive/{target}.zip"
			)
		)

__plugin_pythoncompat__ = ">=2.7,<4"
__plugin_implementation__ = OctoAppPlugin()

__plugin_hooks__ = {
	"octoprint.plugin.softwareupdate.check_config":
	__plugin_implementation__.get_update_information
}
