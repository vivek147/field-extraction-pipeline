"""
Logging utilities for invoice extraction pipeline.

Provides structured logging with file rotation, console output, and metrics tracking.
"""

import logging
import logging.handlers
from pathlib import Path
from typing import Optional


class StructuredLogger:
    """Structured logging with metrics and context."""
    
    def __init__(self, name: str, config_path: Optional[str] = None, log_level: str = "INFO"):
        """
        Initialize structured logger.
        
        Args:
            name: Logger name (usually __name__)
            config_path: Path to log file
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        """
        self.logger = logging.getLogger(name)
        self.logger.setLevel(log_level)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)
        
        # File handler with rotation
        if config_path:
            log_file = Path(config_path)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
            )
            file_handler.setLevel(log_level)
            file_formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
            )
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)
    
    def info(self, msg: str, **kwargs):
        """Log info level message with context."""
        if kwargs:
            msg = f"{msg} | context: {kwargs}"
        self.logger.info(msg)
    
    def error(self, msg: str, exc_info: bool = False, **kwargs):
        """Log error message with optional exception info."""
        if kwargs:
            msg = f"{msg} | context: {kwargs}"
        self.logger.error(msg, exc_info=exc_info)
    
    def warning(self, msg: str, **kwargs):
        """Log warning message."""
        if kwargs:
            msg = f"{msg} | context: {kwargs}"
        self.logger.warning(msg)
    
    def debug(self, msg: str, **kwargs):
        """Log debug message."""
        if kwargs:
            msg = f"{msg} | context: {kwargs}"
        self.logger.debug(msg)


def get_logger(name: str, config_path: Optional[str] = None) -> StructuredLogger:
    """Get or create a structured logger instance."""
    return StructuredLogger(name, config_path)
