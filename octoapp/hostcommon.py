import os
import random
import string
from typing import Optional

# Common functions that the hosts might need to use.
class HostCommon:

    # The length the printer ID should be.
    # Note that the max length for a subdomain part (strings between . ) is 63 chars!
    # Making this a max of 60 chars allows for the service to use 3 chars prefixes for inter-service calls.
    c_OctoAppPrinterIdMaxLength = 60
    c_OctoAppPrinterIdMinLength = 40

    # Returns a new printer Id. This needs to be crypo-random to make sure it's not predictable.
    @staticmethod
    def GeneratePrinterId():
        return ''.join(random.SystemRandom().choice(string.ascii_uppercase + string.digits) for _ in range(HostCommon.c_OctoAppPrinterIdMaxLength))

    @staticmethod
    def IsPrinterIdValid(printerId:Optional[str]) -> bool:
        return printerId is not None and len(printerId) >= HostCommon.c_OctoAppPrinterIdMinLength and len(printerId) <= HostCommon.c_OctoAppPrinterIdMaxLength
    

    # This will restart the plugin or if running in OctoPrint restart OctoPrint!
    # Only use if absolutely needed!
    @staticmethod
    def RestartPlugin():
        # Use os exit, to ensure the process is killed and restarted.
        # pylint: disable=protected-access
        os._exit(0)
