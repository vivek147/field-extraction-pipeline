"""
Configuration management for invoice extraction pipeline.

Handles environment variables, default settings, and validation.
Supports dev, staging, and production environments.
"""

import os
from enum import Enum
from pathlib import Path
from dataclasses import dataclass


class Environment(Enum):
    """Deployment environment."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass
class DocumentModelConfig:
    """Configuration for prebuilt document models."""
    enabled: bool = False
    model_name: str = ""
    device: str = "cpu"  # "cpu" or "cuda"
    batch_size: int = 1
    confidence_threshold: float = 0.7


@dataclass
class LLMVisionConfig:
    """Configuration for LLM vision-based extraction."""
    enabled: bool = False
    endpoint: str = ""
    api_key: str = ""
    model: str = "claude-3-5-sonnet-20241022"
    timeout: int = 60
    max_retries: int = 3
    fallback_on_failure: bool = True


@dataclass
class OCRConfig:
    """Configuration for OCR fallback."""
    enabled: bool = True
    engine: str = "tesseract"  # "tesseract" or "paddleocr"
    language: str = "eng"
    timeout: int = 30


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_path: str = "logs/extraction.log"
    max_bytes: int = 10 * 1024 * 1024  # 10 MB
    backup_count: int = 5


@dataclass
class PipelineConfig:
    """Overall pipeline configuration."""
    environment: Environment
    input_dir: Path
    output_dir: Path
    document_model: DocumentModelConfig
    llm_vision: LLMVisionConfig
    ocr: OCRConfig
    logging: LoggingConfig
    batch_size: int = 10
    max_workers: int = 4
    timeout_per_invoice: int = 300
    retry_failed: bool = True
    max_retries: int = 3


def load_config() -> PipelineConfig:
    """
    Load configuration from environment variables and defaults.
    
    Environment variables:
        INVOICE_ENV: development|staging|production (default: development)
        INVOICE_INPUT_DIR: input directory path
        INVOICE_OUTPUT_DIR: output directory path
        DOCUMENT_MODEL: model name for document extraction (e.g., microsoft/layoutlm-base)
        LLM_VISION_ENDPOINT: API endpoint for vision LLM
        LLM_API_KEY: API key for LLM service
        LLM_MODEL: LLM model name (default: claude-3-5-sonnet-20241022)
        OCR_ENGINE: tesseract|paddleocr (default: tesseract)
        LOG_LEVEL: DEBUG|INFO|WARNING|ERROR (default: INFO)
    """
    env_str = os.environ.get("INVOICE_ENV", "development").lower()
    environment = Environment(env_str)
    
    input_dir = Path(os.environ.get(
        "INVOICE_INPUT_DIR",
        r"E:\Code\poc\batch_1"
    ))
    output_dir = Path(os.environ.get(
        "INVOICE_OUTPUT_DIR",
        r"E:\Code\poc\output"
    ))
    
    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)
    
    return PipelineConfig(
        environment=environment,
        input_dir=input_dir,
        output_dir=output_dir,
        batch_size=int(os.environ.get("BATCH_SIZE", "10")),
        max_workers=int(os.environ.get("MAX_WORKERS", "4")),
        timeout_per_invoice=int(os.environ.get("TIMEOUT_PER_INVOICE", "300")),
        document_model=DocumentModelConfig(
            enabled=bool(os.environ.get("DOCUMENT_MODEL")),
            model_name=os.environ.get("DOCUMENT_MODEL", ""),
            device=os.environ.get("MODEL_DEVICE", "cpu"),
        ),
        llm_vision=LLMVisionConfig(
            enabled=bool(os.environ.get("LLM_VISION_ENDPOINT")),
            endpoint=os.environ.get("LLM_VISION_ENDPOINT", ""),
            api_key=os.environ.get("LLM_API_KEY", ""),
            model=os.environ.get("LLM_MODEL", "claude-3-5-sonnet-20241022"),
            timeout=int(os.environ.get("LLM_TIMEOUT", "60")),
        ),
        ocr=OCRConfig(
            engine=os.environ.get("OCR_ENGINE", "tesseract"),
            language=os.environ.get("OCR_LANGUAGE", "eng"),
        ),
        logging=LoggingConfig(
            level=os.environ.get("LOG_LEVEL", "INFO"),
            file_path=os.environ.get("LOG_FILE", "logs/extraction.log"),
        ),
    )
