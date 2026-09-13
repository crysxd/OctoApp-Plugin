import os

class Version:

    # Parses the common plugin version from the repo root.
    # Throws if the version string can't be found in any of the known files.
    # This logic is shared with the moonraker installer!
    @staticmethod
    def GetPluginVersion(repoRoot:str) -> str:
        # Since OctoPrint says the version must be in the pyproject.toml file, we share the same file to reduce any duplication.
        errors = []
        try:
            return Version._ParsePyProjectVersion(os.path.join(repoRoot, "pyproject.toml"))
        except Exception as e:
            errors.append(str(e))

        # Before the OctoPrint 2.0 packaging migration the version lived in the setup.py file. We fall back to it, so a repo
        # that's mid update or checked out at an older commit can still report a version instead of failing to start at all.
        try:
            return Version._ParseSetupPyVersion(os.path.join(repoRoot, "setup.py"))
        except Exception as e:
            errors.append(str(e))

        raise Exception("Failed to parse the plugin version. "+" ".join(errors))


    # Parses the version out of the [project] section of a pyproject.toml file.
    # Throws if the file can't be found or the version string can't be found.
    @staticmethod
    def _ParsePyProjectVersion(filePath:str) -> str:
        if os.path.exists(filePath) is False:
            raise Exception("Failed to find our repo root project file to parse the version string. Expected Path: "+filePath)

        # Read the file, find the version string. Note that we only accept the version of the [project] section,
        # since other sections can define a version as well.
        inProjectSection = False
        with open(filePath, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines:
                line = line.strip()

                # Keep track of the section we are in.
                if line.startswith("["):
                    inProjectSection = line == "[project]"
                    continue

                if inProjectSection is False or "=" not in line:
                    continue

                if line.split("=", 1)[0].strip() == "version":
                    return Version._ParseQuotedValue(line)

        raise Exception("Failed to find a version in the [project] section of project file: "+filePath)


    # Parses the legacy plugin_version out of a setup.py file.
    # Throws if the file can't be found or the version string can't be found.
    @staticmethod
    def _ParseSetupPyVersion(filePath:str) -> str:
        if os.path.exists(filePath) is False:
            raise Exception("Failed to find our repo root setup file to parse the version string. Expected Path: "+filePath)

        # Read the file, find the version string.
        expectedVersionKey = "plugin_version"
        with open(filePath, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines:
                if line.startswith(expectedVersionKey):
                    return Version._ParseQuotedValue(line)

        raise Exception("Failed to find a line that starts with '"+expectedVersionKey+"' in setup file: "+filePath)


    # Parses the first quoted value out of the given line.
    # Throws if there's no quoted value in the line.
    @staticmethod
    def _ParseQuotedValue(line:str) -> str:
        for quote in ('"', "'"):
            firstQuote = line.find(quote)
            if firstQuote == -1:
                continue
            firstQuote += 1 # Move past it
            secondQuote = line.find(quote, firstQuote)
            if secondQuote == -1:
                continue
            value = line[firstQuote:secondQuote]
            if len(value) > 0:
                return value

        raise Exception("Failed to find a quoted value in version line '"+line+"'")
