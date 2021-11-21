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
import base64
import hashlib
from Crypto import Random
from Crypto.Cipher import AES
import uuid

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
			encryptionKey=None
		)

	def on_after_startup(self):
		self._logger.info("OctoApp started, updating config")
		self.get_config()
		self.get_or_create_encryption_key()

	def get_template_configs(self):
		return [
			dict(type="settings", custom_bindings=False)
		]

	def get_api_commands(self):
		return dict(
			registerForNotifications=[],
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

		# send update, but don't send for 100%
		if (progress < 100):
			self.send_notification(
			   dict(
				   type='printing',
				   fileName=self.last_print_name,
				   progress=self.last_progress,
				   timeLeft=self.last_time_left
			   ),
			   False
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
				),
				True
			)

		elif event == 'PrintFailed' or event == 'PrintCancelled':
			self.last_progress = None
			self.last_print_name = None
			self.send_notification(
				dict(
					type='idle',
					fileName=payload['name']
				),
				False
			)

		elif event == 'FilamentChange' and self.last_progress != None:
			self.send_notification(
				dict(
					type='filament_required',
					fileName=self.last_print_name,
					progress=self.last_progress,
					timeLeft=self.last_time_left,
				),
				True
			 )

		elif event == 'PrintPaused':
			self.send_notification(
				dict(
					type='paused',
					fileName=payload['name'],
					progress=self.last_progress,
					timeLeft=self.last_time_left,
				),
				False
			)

	def send_notification(self, data, highPriority):
		self._logger.debug('send_notification')
		threading.Thread(target=self.do_send_notification, args=[data, highPriority]).start()

	def do_send_notification(self, data, highPriority):
		try:
			self._logger.debug('do_send_notification')
			config = self.get_config()

			# encrypt message and build request body
			data['serverTime'] = int(time.time())
			cipher = AESCipher(self.get_or_create_encryption_key())
			data = cipher.encrypt(json.dumps(data))
			apps = self._settings.get(['registeredApps'])
			if not apps:
				self._logger.debug('No apps registered, skipping notification')
				return

			body=dict(
				targets=apps,
				highPriority=highPriority,
				data=data
			)
			self._logger.debug('Sending notification %s' % body)

			# make request and check 200
			r = requests.post(
				config['sendNotificationUrl'],
				timeout=float(15),
				json=body
			)
			if r.status_code != requests.codes.ok:
				raise Exception('Unexpected response code %d' % r.status_code)

			# delete invalid tokens
			apps = self._settings.get(['registeredApps'])
			self._logger.debug("Before updating apps %s" % apps)
			for fcmToken in r.json()['invalidTokens']:
				apps = [app for app in apps if app['fcmToken'] != fcmToken]
			self._settings.set(['registeredApps'], apps)
			self._settings.save()
			self._logger.debug("Updated apps %s" % apps)
		except Exception as e:
			self._logger.debug("Failed to send notification %s" % e)

	def on_api_command(self, command, data):
		self._logger.info("Recevied command %s" % command)

		if command == 'registerForNotifications':
			fcmToken = data['fcmToken']

			# load apps and filter the given FCM token out
			apps = self._settings.get(['registeredApps'])
			if apps:
				apps = [app for app in apps if app['fcmToken'] != fcmToken]
			else:
				apps = []

			# add app for new registration
			apps.append(
				dict(
					fcmToken=fcmToken,
					instanceId=data['instanceId'],
					displayName=data['displayName'],
					model=data['model'],
					appVersion=data['appVersion'],
					appBuild=data['appBuild'],
					appLanguage=data['appLanguage'],
					lastSeenAt=time.time()
				)
			)

			# save
			self._logger.info("Registered app %s" % fcmToken)
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

	def get_or_create_encryption_key(self):
		key = self._settings.get(['encryptionKey'])
		if key == None:
			key = str(uuid.uuid4())
			self._logger.info("Created new encryption key")
			self._settings.set(['encryptionKey'], key)
		return key

__plugin_pythoncompat__ = ">=2.7,<4"
__plugin_implementation__ = OctoAppPlugin()

__plugin_hooks__ = {
	"octoprint.plugin.softwareupdate.check_config":
	__plugin_implementation__.get_update_information
}

class AESCipher(object):

	def __init__(self, key):
	    self.bs = AES.block_size
	    self.key = hashlib.sha256(key.encode()).digest()

	def encrypt(self, raw):
	    raw = self._pad(raw)
	    iv = Random.new().read(AES.block_size)
	    cipher = AES.new(self.key, AES.MODE_CBC, iv)
	    return base64.b64encode(iv + cipher.encrypt(raw.encode())).decode('utf-8')

	def _pad(self, s):
	    return s + (self.bs - len(s) % self.bs) * chr(self.bs - len(s) % self.bs)
