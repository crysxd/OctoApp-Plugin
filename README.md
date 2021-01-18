# OctoLight
A simple plugin that adds a button to the navigation bar for toggleing a GPIO pin on the Raspberry Pi. I use it for turning ON and OFF the light on my 3D printer.

![WebUI interface](img/screenshoot.png)

## Setup
Install via the bundled [Plugin Manager](https://docs.octoprint.org/en/master/bundledplugins/pluginmanager.html)
or manually using this URL:

	https://github.com/gigibu5/OctoLight/archive/master.zip

## Configuration
There is an option in the settings menu for changing the GPIO the button trigeres. **IMPORTANT:** the pins are saved in the board layout, not by GPIO naming scheme. Below is a picture of the Raspberry Pies GPIO configuration. The correct numbers are those written in gray on the picture bellow.

![Raspberry Pi GPIO](img/rpi_gpio.png)