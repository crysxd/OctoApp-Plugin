#!/bin/bash

cd ..

echo ""
echo "Running pylint on the OctoApp Module..."
pylint ./octoapp/
echo "Running pylint on the OctoPrint Module..."
pylint ./octoprint_octoapp/
echo "Running pylint on the Moonraker Module..."
pylint ./moonraker_octoapp/
echo "Running pylint on the Elegoo Module..."
pylint ./elegoo_octoapp/
echo "Running pylint on the Bambu Module..."
pylint ./bambu_octoapp/
echo "Running pylint on the Linux Host Module..."
pylint ./linux_host/
echo "Running pylint on the Installer Module..."
pylint ./py_installer/

echo "Running pyright..."
pyright

echo "Running ruff..."
ruff check

cd developer