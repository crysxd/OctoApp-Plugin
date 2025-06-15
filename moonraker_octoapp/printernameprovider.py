from abc import abstractmethod

class IPrinterNameProvider:

    @abstractmethod
    def GetPrinterName(self) -> str:
        pass
