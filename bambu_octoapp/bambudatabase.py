import json
import time
import threading
import ftplib
import ssl
import uuid
from io import BytesIO
from typing import List, Dict, Any

from octoapp.sentry import Sentry
from octoapp.logging import LoggerLike
from linux_host.config import Config

# Implements logic that deals with the moonraker database.
class BambuFtpDatabase:

    def __init__(self, logger:LoggerLike,printerId:str, pluginVersion:str, config:Config) -> None:
        self.PluginVersion = pluginVersion
        self.PresenceAnnouncementRunning = False
        self.PrinterId = printerId
        self.Logger = logger
        self.CachedEncryptionKey = None
        self._continuouslyAnnouncePresence()
        self.Host = config.GetStr(Config.SectionCompanion, Config.CompanionKeyIpOrHostname, None)
        self.User = "bblp"
        self.Port = 990
        self.DbPath = "/octoapp_db"
        self.Password = config.GetStr(Config.SectionBambu, Config.BambuAccessToken, None)


    def GetAppsEntry(self) -> List[Dict[str,Any]]:
        self.Logger.debug("Getting apps")
        return self._ReadDatabaseFiles("app_")


    def GetOrCreateEncryptionKey(self) -> str:
        self.Logger.debug("Getting or creating encryption key")
        encryptionKeyName = "enctypion_key"

        if self.CachedEncryptionKey is not None:
            self.Logger.debug("Reusing cached")
            return self.CachedEncryptionKey

        self.Logger.debug("Checking if in database...")
        if encryptionKeyName in self._ListDatabaseEntries():
            self.Logger.debug("Loading from database...")
            key = self._ReadDatabaseFile(encryptionKeyName).get(encryptionKeyName)
            if key is not None:
                self.CachedEncryptionKey = key
                return key

        self.Logger.info("No key in database, creating new one...")
        key = str(uuid.uuid4())
        self._WriteDatabaseFile(encryptionKeyName, {encryptionKeyName: key})
        self.CachedEncryptionKey = key
        return key


    def RemoveAppEntries(self, apps:List[str]):
        self.Logger.info(f"Removing apps: {apps}")
        self._DeleteDatabaseFiles(apps)


    def EnsureOctoAppDatabaseEntry(self):
        # Useful for debugging.
        self._Debug_EnumerateDataBase()

        # We use a few database entries under our own name space to share information with apps and other plugins.
        # Note that since these are used by 3rd party systems, they must never change. We also use this for our frontend.
        if self.PresenceAnnouncementRunning is False:
            self.PresenceAnnouncementRunning = True
            self._continuouslyAnnouncePresence()


    def _Debug_EnumerateDataBase(self):
        try:
            self.Logger.error(f"_Debug_EnumerateDataBase: {self._ListDatabaseEntries()}")
        except Exception as e:
            Sentry.ExceptionNoSend("_Debug_EnumerateDataBase exception.", e)


    def _continuouslyAnnouncePresence(self):
        t = threading.Thread(target=self._doContinuouslyAnnouncePresence)
        t.daemon = True
        t.start()


    def _doContinuouslyAnnouncePresence(self):
        self.Logger.info("Starting continuous update")
        while True:
            try:
                self._WriteDatabaseFile(
                    name="plugin",
                    content={
                        "pluginVersion": self.PluginVersion,
                        "lastSeen": time.time(),
                        "printerId": self.PrinterId,
                        "encryptionKey": self.GetOrCreateEncryptionKey()
                    }
                )
                time.sleep(60)
            except Exception as e:
                Sentry.ExceptionNoSend("Failed to update presence", e)
                time.sleep(240)

    def _GetConnection(self) -> ftplib.FTP_TLS:
        """Create and return an FTPS connection with disabled certificate verification."""
        # Create SSL context that doesn't verify certificates
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        if self.Host is None or self.Password is None:
            raise Exception("Missing host or password for FTP connection!")

        # Create FTPS connection
        self.Logger.debug(f"Connecting FTP {self.User}:{self.Password}@{self.Host}:{self.Port}")
        ftp = ImplicitFTP_TLS(context=context)
        ftp.connect(host=self.Host, port=self.Port, timeout=10)
        ftp.login(user='bblp', passwd=self.Password)
        ftp.prot_p()

        return ftp


    def _WriteDatabaseFile(self, name: str, content: Dict[str, Any]):
        """Upload content to FTPS server as a file."""
        self.Logger.debug(f"Writing to {name}")
        ftp = self._GetConnection()
        try:
            # Convert string to bytes and upload
            json_content = json.dumps(content)
            data = BytesIO(json_content.encode('utf-8'))

            # Esnure dir exists
            try:
                ftp.cwd(self.DbPath)
            except ftplib.error_perm as e:
                if "550" in str(e):  # Directory not found or empty
                    self.Logger.debug("Database folder missing, creating new one...")
                    ftp.mkd(self.DbPath)

            ftp.storbinary(f'STOR {self._GetDbPath(name)}', data)
        except Exception as e:
            # Check if it's just an SSL shutdown timeout (file likely uploaded successfully)
            if "The read operation timed out" == str(e):
                self.Logger.debug(f"SSL shutdown timeout for {name} - file likely uploaded successfully")
            else:
                raise  # Re-raise other exceptions
        finally:
            try:
                ftp.quit()
            except Exception:
                # Ignore quit errors - common with SSL timeout issues
                try:
                    ftp.close()
                except Exception:
                    pass


    def _ReadDatabaseFile(self, name: str) -> Dict[str, Any]:
        """Download file from FTPS server and parse as JSON."""
        self.Logger.debug(f"Reading from {name}")
        ftp = self._GetConnection()
        try:
            # Download file content
            data = BytesIO()
            ftp.retrbinary(f'RETR {self._GetDbPath(name)}', data.write)

            # Parse JSON content
            content = data.getvalue().decode('utf-8')
            return json.loads(content)
        except ftplib.error_perm as e:
            if "550" in str(e):  # File not found
                return {}
            raise
        finally:
            ftp.quit()


    def _ReadDatabaseFiles(self, prefix: str) -> List[Dict[str, Any]]:
        """Read all files with the given prefix and return as a dictionary.
        Returns a dictionary mapping filename to file content (parsed JSON).
        """
        self.Logger.debug(f"Reading all with prefix {prefix}")
        ftp = self._GetConnection()
        try:
            # Get all files
            files:List[str] = []
            file_lines:List[str] = []
            ftp.retrlines(f'LIST {self.DbPath}', file_lines.append)

            # Parse LIST output to extract filenames
            files = []
            for line in file_lines:
                # Skip directories (lines starting with 'd')
                if line.startswith('d'):
                    continue
                # Extract filename (last part after splitting by spaces)
                parts = line.split()
                if parts:
                    filename = parts[-1]  # Last part is usually the filename
                    files.append(filename)

            # Get all files with the specified prefix
            matching_files = [f for f in files if f.startswith(prefix)]

            results:List[Dict[str,Any]] = []
            for filename in matching_files:
                try:
                    # Download file content
                    data = BytesIO()
                    ftp.retrbinary(f'RETR {self._GetDbPath(filename)}', data.write)

                    # Parse JSON content
                    content = data.getvalue().decode('utf-8')
                    results.append(json.loads(content))
                except Exception as e:
                    Sentry.ExceptionNoSend(f"Failed to read {filename}", e)

            self.Logger.debug(f"Read: {results}")
            return results
        finally:
            ftp.quit()


    def _ListDatabaseEntries(self) -> List[str]:
        """List filenames in the specified directory."""
        self.Logger.debug("Listing database")
        ftp = self._GetConnection()
        try:
            # Get directory listing
            files:List[str] = []
            ftp.retrlines(f'NLST {self.DbPath}', files.append)
            self.Logger.debug(f"Listing result: {files}")
            return files
        except ftplib.error_perm as e:
            if "550" in str(e):  # Directory not found or empty
                return []
            raise
        finally:
            ftp.quit()


    def _DeleteDatabaseFiles(self, names: List[str]):
        """Delete a set of files from FTPS server.

        Returns a dictionary mapping filename to success status.
        """
        self.Logger.debug(f"Deleting: {names}")
        ftp = self._GetConnection()
        results = {}
        try:
            for name in names:
                ftp.delete(self._GetDbPath(name))
                results[name] = True
        finally:
            ftp.quit()


    def _GetDbPath(self, name:str) -> str:
        return f"{self.DbPath}/{name}"


class ImplicitFTP_TLS(ftplib.FTP_TLS):
    def __init__(self, *args, **kwargs): # type: ignore
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self): # type: ignore
        return self._sock # type: ignore

    @sock.setter
    def sock(self, value): # type: ignore
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value # type: ignore

    def ntransfercmd(self, cmd, rest=None):
        """
        Increases relability with some printers
        Courtesy @WolfwithSword
        """
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p: # type: ignore
            session = self.sock.session # type: ignore
            if isinstance(self.sock, ssl.SSLSocket): # type: ignore
                session = self.sock.session
            conn = self.context.wrap_socket(conn,
                                            server_hostname=self.host,
                                            session=session)
        return conn, size
