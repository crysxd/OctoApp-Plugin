from typing import List

from octoapp.appsstorage import AppInstance
from octoapp.appsstorage import AppStoragePlatformHelper
from .bambudatabase import BambuFtpDatabase


class BambuAppStorage(AppStoragePlatformHelper):

    def __init__(self, database: BambuFtpDatabase):
        self.First = False
        self.Database = database


    # !! Platform Command Handler Interface Function !!
    #
    # This must return a list of AppInstance
    #
    def GetAllApps(self) -> List[AppInstance]:
        apps = self.Database.GetAppsEntry()
        return list(map(AppInstance.FromDict, apps))


    # !! Platform Command Handler Interface Function !!
    #
    # This must receive a lsit of AppInstnace
    #
    def RemoveApps(self, apps:List[AppInstance]):
        tokens = list(map(lambda app: app.FcmToken, apps))
        self.Database.RemoveAppEntries(tokens)

    # !! Platform Command Handler Interface Function !!
    #
    # This must receive a lsit of AppInstnace
    #
    def GetOrCreateEncryptionKey(self):
        return self.Database.GetOrCreateEncryptionKey()
