from typing import Any, Union
from logging import LoggerAdapter, Logger

class LoggerLike:
    def info(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def error(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def debug(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def warning(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def critical(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        pass

    def exception(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        pass

class TaggedLoggingAdapter(LoggerAdapter):
    def __init__(self, logger: Union[Logger, LoggerLike], tag:str):
        self.originalLogger: Logger
        if isinstance(logger, Logger):
            self.originalLogger = logger
        elif isinstance(logger, TaggedLoggingAdapter):
            self.originalLogger = logger.originalLogger
        else:
            raise Exception("Can't use {logger}")

        self.tag: str
        if isinstance(logger, Logger):
            self.tag = tag
        elif isinstance(logger, TaggedLoggingAdapter):
            self.tag = f"{logger.tag}/{tag}"

        super().__init__(self.originalLogger, {})  # type: ignore


    def process(self, msg, kwargs):
        return f"{self.tag:<24} | {msg}", kwargs
