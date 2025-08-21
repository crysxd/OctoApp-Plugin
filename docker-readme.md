# OctoApp Companion Docker Image

OctoApp's Companion Docker image works with: ✅

- [OctoApp Bambu Companion Plugin](https://github.com/crysxd/OctoApp-Plugin/wiki/Installation-for-BambuLab) - OctoApp for Bambu Lab 3D printers.
- [OctoApp Elegoo Companion Plugin](https://github.com/crysxd/OctoApp-Plugin/wiki/Installation-on-Elegoo-Centauri) - OctoApp for Elegoo Centauri & Centauri Carbon 3D printers.
- [OctoApp Klipper Companion](https://github.com/crysxd/OctoApp-Plugin/wiki/Installation-on-Klipper) - OctoApp for Klipper 3D printers.

OctoApp's Companion Docker image **does not work with:** ⛔

- [OctoApp For OctoPrint](https://github.com/crysxd/OctoApp-Plugin/wiki/Installation-on-OctoPrint) - Install the OctoApp plugin directly in OctoPrint.
- [OctoApp For Klipper](https://github.com/crysxd/OctoApp-Plugin/wiki/Installation-on-Klipper) - If you can install OctoApp on the same device running Klipper, it's the recommended setup. Otherwise use the OctoApp Klipper Companion.


🤔 Confused? [Follow our step-by-step guide](https://github.com/crysxd/OctoApp-Plugin/wiki) to find the right version for your 3D printer!

Based off this Docker Image: https://hub.docker.com/r/octoeverywhere/octoeverywhere

Based off this Docker Compose: [GitHub Repo File](https://github.com/QuinnDamerell/OctoPrint-OctoEverywhere/blob/master/docker-compose.yml)

## Required Image Setup Information

There are three modes of the OctoApp docker image depending on what 3D printer you're trying to use.

- `COMPANION_MODE=bambu` - Bambu Companion Plugin for Bambu Lab 3D printer.
- `COMPANION_MODE=elegoo` - Elegoo Companion Plugin for the Elegoo Centauri & Centauri Carbon.
- `COMPANION_MODE=klipper` - For Klipper / Moonraker based 3D printers.

Different companion modes need different printer information.

### Bambu Companion Plugin

To use Bambu Companion Plugin, you need to get the following information.

- Your printer's Access Code - https://octoeverywhere.com/s/access-code
- Your printer's Serial Number - https://octoeverywhere.com/s/bambu-sn
- Your printer's IP Address - https://octoeverywhere.com/s/bambu-ip

These three values must be set as environment vars when you first run the container. Once the container is run, you don't need to include them again, unless you want to update the values.

- ACCESS_CODE=(code)
- SERIAL_NUMBER=(serial number)
- PRINTER_IP=(ip address)

### Elegoo Companion Plugin

To use Elegoo Companion Plugin, you need to get the following information.

- Your Elegoo printer's IP address. - https://octoeverywhere.com/s/elegoo-ip

The IP address must be set as an environment var when you first run the container. Once the container is run, you don't need to include them again, unless you want to update the values.

- PRINTER_IP=(ip address)

### Klipper Companion

To use the Klipper Companion, you need to get the following information.

- Your printer's IP address.
- (optional) Moonraker's server port. Defaults to 7125.
- (optional) Moonraker API key. Defaults to None. If your Moonraker server requires auth, you can generate an API key in Mainsail or Fluidd.
- (optional) Your web frontend's server port. Defaults to 80.

These three values must be set as environment vars when you first run the container. Once the container is run, you don't need to include them again, unless you want to update the values.

- PRINTER_IP=(ip address)
- MOONRAKER_PORT=(port)
- MOONRAKER_API_KEY=(apiKey)
- WEBSERVER_PORT=(port)

## Required Persistent Storage

You must map the `/data` folder in your docker container to a directory on your computer so the plugin can write data that will remain between runs. Failure to do this will require relinking the plugin when the container is destroyed or updated.

# Running The Docker Image

## Building The Image Locally

You can build the docker image locally if you prefer, use the following command.

`docker build -t octoapp .`

## Using Docker Compose

Using docker compose is the easiest way to run the OctoApp Companion Plugin is using docker image.

- Install [Docker and Docker Compose](https://docs.docker.com/compose/install/linux/)
- Clone this repo
- Edit the `./docker-compose.yml` file to enter your environment information.
- Run `docker compose up -d`
- Follow the "Linking Your OctoApp Companion" to link the plugin with your account.

## Using Docker

Docker compose is a fancy wrapper to run docker containers. You can also run docker containers manually.

Use a command like this example, but update the required vars.

`docker run --name octoapp -e COMPANION_MODE=<mode> -e PRINTER_IP=<ip address> (add other required env vars) -v /your/local/path:/data -d octoapp`