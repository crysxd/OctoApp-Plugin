import random
import string

# Common functions that the hosts might need to use.
class HostCommon:

    # The length the printer ID should be.
    # Note that the max length for a subdomain part (strings between . ) is 63 chars!
    # Making this a max of 60 chars allows for the service to use 3 chars prefixes for inter-service calls.
    c_OctoAppPrinterIdMaxLength = 60
    c_OctoAppPrinterIdMinLength = 40

    # These are the bounds for the private keys. Originally they were 128chars, but after a change we moved them
    # down to 80, which is still way more than enough. But some older installs still use the 128 length, so we have to allow it.
    c_OctoAppPrivateKeyMinLength = 80
    c_OctoAppPrivateKeyMaxLength = 128

    # The url for the add printer process.
    c_OctoAppAddPrinterUrl = "https://octoapp.com/getstarted"

    # The main URL octoclients use to connect.
    # MUST be wss!
    c_OctoAppOctoClientWsUri = "wss://starport-v1.octoapp.com/octoclientws"

    # Returns a new printer Id. This needs to be crypo-random to make sure it's not predictable.
    @staticmethod
    def GeneratePrinterId():
        return ''.join(random.SystemRandom().choice(string.ascii_uppercase + string.digits) for _ in range(HostCommon.c_OctoAppPrinterIdMaxLength))

    # Returns a new private key. This needs to be crypo-random to make sure it's not predictable.
    @staticmethod
    def GeneratePrivateKey():
        return ''.join(random.SystemRandom().choice(string.ascii_uppercase + string.ascii_lowercase + string.digits) for _ in range(HostCommon.c_OctoAppPrivateKeyMinLength))

    @staticmethod
    def IsPrinterIdValid(printerId):
        return printerId is not None and len(printerId) >= HostCommon.c_OctoAppPrinterIdMinLength and len(printerId) <= HostCommon.c_OctoAppPrinterIdMaxLength

    @staticmethod
    def IsPrivateKeyValid(privateKey):
        return privateKey is not None and len(privateKey) >= HostCommon.c_OctoAppPrivateKeyMinLength and len(privateKey) <= HostCommon.c_OctoAppPrivateKeyMaxLength

    @staticmethod
    def GetAddPrinterUrl(printerId, isOctoPrint):
        sourceGetArg = "isFromOctoPrint=true"
        if isOctoPrint is False:
            sourceGetArg = "isFromKlipper=true"
        # Note this must have at least one ? and arg because users of it might append &source=blah
        return HostCommon.c_OctoAppAddPrinterUrl + "?" + sourceGetArg + "&" + "printerid=" + printerId
