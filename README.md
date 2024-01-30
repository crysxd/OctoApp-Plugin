
# OctoApp plugin
A plugin providing extra functionality to OctoApp:

- Remote push notification for events like print completion or filament change with end-to-end encryption
- Remote push notifications for your print progress with end-to-end encryption

Get OctoApp on Google Play!



[<img src="https://github.com/crysxd/OctoApp-Plugin/blob/1813c8145887d2862373a97279f56f4542c47ee3/images/play-badge.png" width="200">](https://play.google.com/store/apps/details?id=de.crysxd.octoapp&hl=en&gl=US)  [<img src="https://github.com/crysxd/OctoApp-Plugin/blob/1813c8145887d2862373a97279f56f4542c47ee3/images/app-store-badge.png" width="200">](https://apps.apple.com/us/app/octoapp-for-octoprint/id1658133862)

## Setup OctoPrint

1. Open your **OctoPrint web interface**
2. Open the **settings** using the 🔧 (wrench) icon in the top right
3. Select the **Plugin Manager** in the left column 
4. Click **+ Get More**
5. Search for **OctoApp**
6. Click **Install**
7. Reboot OctoPrint when promted

The app will automatically register itself with the plugin the next time you use the app. In the OctoPrint settings, you will find a list with all connected devices.


## Setup Moonraker

1. Open a terminal on your Klipper host via SSH
2. Run `/bin/bash -c "$(curl -fsSL https://octoapp.eu/install.sh)"`  
This will clone this repository and guide your through the setup process

The app will automatically register itself with the plugin the next time you use the app. The plugin will run as a Linux service in the background, you can start or stop it via Mainsail or Fluidd. To see a list of registered apps open http://_yourmoonraker_/server/database/item?namespace=octoapp

## Configuration
Nothing to configure! OctoApp will connect automatically to the plugin

## Issues / Rquests
Please use the app's bug report function in case of any issues. Feature requests can go to [GitLab](https://gitlab.com/realoctoapp/octoapp/-/issues/).
