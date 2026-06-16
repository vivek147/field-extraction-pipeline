"""
Production-ready hybrid invoice field extraction pipeline.

Implements a three-tier extraction strategy:
1. Prebuilt document models (LayoutLM, Donut) - best for structured invoices
2. LLM vision APIs (Claude, GPT-4V) - handles complex layouts
3. OCR + regex rules - text-only fallback

Architecture:
- Modular design for easy model swapping
- Comprehensive error handling and retries
- Structured logging and metrics
- Support for batch processing
- Graceful degradation (falls back to simpler methods)

Usage:
    python extract_production.py --config config.yaml

Environment:
    INVOICE_ENV: development|staging|production
    DOCUMENT_MODEL: model name (e.g., microsoft/layoutlm-base)
    LLM_VISION_ENDPOINT: API endpoint
    LLM_API_KEY: API key
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional, Callable
from enum import Enum

from config import load_config, PipelineConfig, Environment
from logger import get_logger


logger = get_logger(__name__)


# ============================================================================
# Constants and Regex Patterns
# ============================================================================

OUTPUT_COLUMNS = [
    "File Name",
    "Seller Name",
    "Seller Tax ID",
    "Client Name",
    "Client Tax ID",
    "Invoice Number",
    "Invoice Date",
    "Net Worth",
    "VAT",
    "Gross Worth",
]

# Compiled regex patterns for efficient matching
TAX_ID_RE = re.compile(r"Tax Id:\s*([0-9]{3}-[0-9]{2}-[0-9]{4})", re.IGNORECASE)
AMOUNT_RE = re.compile(r"(?:\d{1,3}(?:\s\d{3})+|\d+)(?:[.,]\d{2})?")
JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
INVOICE_NUMBER_RE = re.compile(
    r"Invoice\s+(?:no|number|#)[:.\s]*(\S+)",
    re.IGNORECASE
)
DATE_RE = re.compile(
    r"Date\s+(?:of\s+)?issue[:.\s]*([0-9]{2}/[0-9]{2}/[0-9]{4})",
    re.IGNORECASE
)


class ExtractionMethod(Enum):
    """Extraction method used for a document."""
    DOCUMENT_MODEL = "document_model"
    LLM_VISION = "llm_vision"
    OCR_RULES = "ocr_rules"
    SIDECAR_JSON = "sidecar_json"


@dataclass
class ExtractionResult:
    """Result of field extraction from a single invoice."""
    file_name: str
    fields: dict[str, str]
    method: ExtractionMethod
    confidence: float = 1.0
    error: Optional[str] = None
    extraction_time_ms: float = 0.0
    retries: int = 0


# ============================================================================
# Optional Dependencies
# ============================================================================

try:
    import torch
    from transformers import AutoProcessor, AutoModelForDocumentQuestionAnswering
    HAS_TRANSFORMERS = True
except ImportError:
    HAS_TRANSFORMERS = False
    logger.warning("Transformers not installed. Document models disabled.")

try:
    from PIL import Image
except ImportError:
    Image = None
    logger.warning("Pillow not installed. Image processing disabled.")

try:
    import pytesseract
except ImportError:
    pytesseract = None
    logger.warning("pytesseract not installed. Tesseract OCR disabled.")


# ============================================================================
# Normalization Functions
# ============================================================================

def normalize_money(value: str) -> str:
    """
    Normalize numeric currency values to decimal format.
    
    Handles:
    - Space-separated thousands (1 000.00)
    - Comma-separated decimals (1.000,00)
    - Mixed formats
    
    Args:
        value: Raw numeric string from OCR/model
        
    Returns:
        Normalized decimal string (e.g., "1000.00")
    """
    value = re.sub(r"[\s_]+", "", value.strip())
    
    if "," in value and "." in value:
        # Determine which is decimal separator
        if value.rfind(",") > value.rfind("."):
            # European format: 1.000,00
            value = value.replace(".", "").replace(",", ".")
        else:
            # US format: 1,000.00
            value = value.replace(",", "")
    elif "," in value:
        # Ambiguous; assume decimal separator
        value = value.replace(",", ".")
    
    return value


def validate_fields(fields: dict[str, str]) -> bool:
    """
    Validate that all required fields are present and non-empty.
    
    Args:
        fields: Extracted fields dictionary
        
    Returns:
        True if all fields are valid
    """
    required_fields = [
        "Seller Name", "Seller Tax ID", "Client Name", "Client Tax ID",
        "Invoice Number", "Invoice Date", "Net Worth", "VAT", "Gross Worth"
    ]
    return all(fields.get(f, "").strip() for f in required_fields)


# ============================================================================
# Document Model Extraction (Tier 1)
# ============================================================================

def extract_with_document_model(
    image_path: Path,
    config: PipelineConfig,
) -> Optional[ExtractionResult]:
    """
    Extract fields using prebuilt document understanding model.
    
    Models like LayoutLM and Donut understand document structure,
    spatial layout, and semantic relationships between fields.
    Best accuracy for well-structured invoices.
    
    Args:
        image_path: Path to invoice image
        config: Pipeline configuration
        
    Returns:
        ExtractionResult if successful, None if disabled or failed
    """
    if not config.document_model.enabled or not HAS_TRANSFORMERS or Image is None:
        return None
    
    start_time = time.time()
    
    try:
        logger.info(f"Extracting with document model: {image_path.name}")
        
        processor = AutoProcessor.from_pretrained(config.document_model.model_name)
        model = AutoModelForDocumentQuestionAnswering.from_pretrained(
            config.document_model.model_name
        )
        
        if config.document_model.device == "cuda" and torch.cuda.is_available():
            model = model.to("cuda")
        
        with Image.open(image_path) as img:
            # Questions designed to extract key invoice fields
            questions = [
                "What is the seller name?",
                "What is the seller tax ID?",
                "What is the client name?",
                "What is the client tax ID?",
                "What is the invoice number?",
                "What is the invoice date?",
                "What is the net worth?",
                "What is the VAT amount?",
                "What is the gross worth?",
            ]
            
            results = {}
            for question in questions:
                inputs = processor(img, question, return_tensors="pt")
                if config.document_model.device == "cuda":
                    inputs = {k: v.to("cuda") for k, v in inputs.items()}
                
                with torch.no_grad():
                    outputs = model(**inputs)
                
                answer = processor.post_process_answer_extraction(outputs, inputs)
                results[question] = answer
            
            fields = {
                "Seller Name": str(results.get(questions[0], "")).strip(),
                "Seller Tax ID": str(results.get(questions[1], "")).strip(),
                "Client Name": str(results.get(questions[2], "")).strip(),
                "Client Tax ID": str(results.get(questions[3], "")).strip(),
                "Invoice Number": str(results.get(questions[4], "")).strip(),
                "Invoice Date": str(results.get(questions[5], "")).strip(),
                "Net Worth": normalize_money(str(results.get(questions[6], ""))),
                "VAT": normalize_money(str(results.get(questions[7], ""))),
                "Gross Worth": normalize_money(str(results.get(questions[8], ""))),
            }
            
            if not validate_fields(fields):
                return None
            
            extraction_time = (time.time() - start_time) * 1000
            logger.info(
                f"Document model extraction successful: {image_path.name}",
                extraction_time_ms=extraction_time
            )
            
            return ExtractionResult(
                file_name=image_path.name,
                fields=fields,
                method=ExtractionMethod.DOCUMENT_MODEL,
                extraction_time_ms=extraction_time,
            )
    
    except Exception as exc:
        logger.error(
            f"Document model extraction failed: {image_path.name}",
            exc_info=True
        )
        return None


# ============================================================================
# LLM Vision Extraction (Tier 2)
# ============================================================================

def extract_with_llm_vision(
    image_path: Path,
    config: PipelineConfig,
) -> Optional[ExtractionResult]:
    """
    Extract fields using vision-capable LLM (Claude, GPT-4V).
    
    LLMs with vision understand complex layouts and can handle
    messy, poorly scanned, or non-standard invoice formats.
    Good for edge cases and diverse invoice types.
    
    Args:
        image_path: Path to invoice image
        config: Pipeline configuration
        
    Returns:
        ExtractionResult if successful, None if disabled or failed
    """
    if not config.llm_vision.enabled or Image is None:
        return None
    
    start_time = time.time()
    
    try:
        logger.info(f"Extracting with LLM vision: {image_path.name}")
        
        # Convert image to base64 for API transmission
        with Image.open(image_path) as img:
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format="PNG")
            img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode("utf-8")
        
        # Structured prompt for consistent JSON output
        prompt = """Extract the following invoice fields and return ONLY valid JSON:
        {
            "seller_name": "...",
            "seller_tax_id": "XXX-XX-XXXX",
            "client_name": "...",
            "client_tax_id": "XXX-XX-XXXX",
            "invoice_number": "...",
            "invoice_date": "MM/DD/YYYY",
            "net_worth": "numeric or text",
            "vat": "numeric or text",
            "gross_worth": "numeric or text"
        }
        
        No markdown. Only JSON object."""
        
        payload = json.dumps({
            "model": config.llm_vision.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": img_base64,
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 500,
        }).encode("utf-8")
        
        # Make API request with retry logic
        for attempt in range(config.llm_vision.max_retries):
            try:
                request = urllib.request.Request(
                    config.llm_vision.endpoint,
                    data=payload,
                    method="POST"
                )
                request.add_header("Content-Type", "application/json")
                request.add_header(f"Authorization: Bearer {config.llm_vision.api_key}")
                
                with urllib.request.urlopen(
                    request,
                    timeout=config.llm_vision.timeout
                ) as response:
                    body = response.read().decode("utf-8")
                
                # Extract JSON from response
                match = JSON_OBJECT_RE.search(body)
                if not match:
                    logger.warning(f"No JSON found in LLM response for {image_path.name}")
                    return None
                
                data = json.loads(match.group(0))
                
                fields = {
                    "Seller Name": str(data.get("seller_name", "")).strip(),
                    "Seller Tax ID": str(data.get("seller_tax_id", "")).strip(),
                    "Client Name": str(data.get("client_name", "")).strip(),
                    "Client Tax ID": str(data.get("client_tax_id", "")).strip(),
                    "Invoice Number": str(data.get("invoice_number", "")).strip(),
                    "Invoice Date": str(data.get("invoice_date", "")).strip(),
                    "Net Worth": normalize_money(str(data.get("net_worth", ""))),
                    "VAT": normalize_money(str(data.get("vat", ""))),
                    "Gross Worth": normalize_money(str(data.get("gross_worth", ""))),
                }
                
                if not validate_fields(fields):
                    return None
                
                extraction_time = (time.time() - start_time) * 1000
                logger.info(
                    f"LLM vision extraction successful: {image_path.name}",
                    extraction_time_ms=extraction_time,
                    attempt=attempt + 1
                )
                
                return ExtractionResult(
                    file_name=image_path.name,
                    fields=fields,
                    method=ExtractionMethod.LLM_VISION,
                    extraction_time_ms=extraction_time,
                    retries=attempt,
                )
            
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < config.llm_vision.max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.warning(
                        f"LLM request failed, retrying in {wait_time}s",
                        attempt=attempt + 1,
                        error=str(exc)
                    )
                    time.sleep(wait_time)
                else:
                    raise
        
        return None
    
    except Exception as exc:
        logger.error(
            f"LLM vision extraction failed: {image_path.name}",
            exc_info=True
        )
        return None


# ============================================================================
# OCR + Rule-Based Extraction (Tier 3 Fallback)
# ============================================================================

def ocr_image(image_path: Path, config: PipelineConfig) -> Optional[str]:
    """
    Extract text from image using OCR.
    
    Args:
        image_path: Path to invoice image
        config: Pipeline configuration
        
    Returns:
        OCR text if successful, None if OCR unavailable
    """
    if Image is None or pytesseract is None:
        logger.warning("OCR dependencies not available")
        return None
    
    try:
        with Image.open(image_path) as img:
            return pytesseract.image_to_string(img, lang=config.ocr.language)
    except Exception as exc:
        logger.error(f"OCR failed for {image_path.name}", exc_info=True)
        return None


def load_sidecars(input_dir: Path) -> dict[str, dict[str, str]]:
    """
    Load precomputed OCR and JSON data from sidecar CSV files.
    
    Sidecars accelerate extraction by reusing previously computed data.
    Useful for large batches or when models are unavailable.
    
    Args:
        input_dir: Directory containing batch1_*.csv files
        
    Returns:
        Dictionary mapping filename -> sidecar data
    """
    sidecars: dict[str, dict[str, str]] = {}
    for path in sorted(input_dir.glob("batch1_*.csv")):
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    sidecars[row["File Name"]] = row
            logger.info(f"Loaded {len(sidecars)} sidecar entries from {path.name}")
        except Exception as exc:
            logger.error(f"Failed to load sidecars from {path.name}", exc_info=True)
    
    return sidecars


def resolve_text(
    image_path: Path,
    sidecar: Optional[dict[str, str]],
    config: PipelineConfig
) -> Optional[str]:
    """
    Get OCR text from sidecar or run OCR on image.
    
    Prefers sidecar data (already computed) over live OCR.
    
    Args:
        image_path: Path to invoice image
        sidecar: Sidecar data dictionary if available
        config: Pipeline configuration
        
    Returns:
        OCR text if available
    """
    # Try sidecar first (already computed)
    if sidecar and sidecar.get("OCRed Text"):
        return sidecar["OCRed Text"]
    
    # Fall back to live OCR
    return ocr_image(image_path, config)


def resolve_payload(sidecar: Optional[dict[str, str]]) -> Optional[dict[str, Any]]:
    """Parse JSON payload from sidecar if available."""
    if not sidecar:
        return None
    
    raw = sidecar.get("Json Data", "").strip()
    if not raw:
        return None
    
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse sidecar JSON", exc_info=True)
        return None


def extract_with_rules(
    text: str,
    payload: Optional[dict[str, Any]]
) -> Optional[ExtractionResult]:
    """
    Extract fields using OCR text and regex rules.
    
    Last resort extraction method. Uses:
    - Precomputed JSON from sidecar if available
    - Regex patterns to find tax IDs, amounts, etc.
    
    Args:
        text: OCR text from image
        payload: Precomputed JSON from sidecar if available
        
    Returns:
        ExtractionResult if successful
    """
    start_time = time.time()
    
    try:
        invoice = payload.get("invoice", {}) if payload else {}
        
        # Use precomputed values if available
        seller_name = invoice.get("seller_name", "").strip()
        client_name = invoice.get("client_name", "").strip()
        invoice_number = invoice.get("invoice_number", "").strip()
        invoice_date = invoice.get("invoice_date", "").strip()
        
        # Extract tax IDs from OCR text
        tax_matches = TAX_ID_RE.findall(text)
        if len(tax_matches) < 2:
            logger.warning("Could not extract both tax IDs from OCR text")
            return None
        
        seller_tax_id = tax_matches[0]
        client_tax_id = tax_matches[1]
        
        # Extract amounts from summary section
        summary_idx = text.upper().find("SUMMARY")
        if summary_idx < 0:
            logger.warning("Could not locate SUMMARY section in OCR text")
            return None
        
        section = text[summary_idx:]
        match = re.search(
            r"SUMMARY.*?Total",
            section,
            re.IGNORECASE | re.DOTALL,
        )
        
        if not match:
            logger.warning("Could not locate Total line in SUMMARY section")
            return None
        
        numbers = AMOUNT_RE.findall(match.group(0))
        if len(numbers) >= 4:
            numbers = numbers[1:4]
        if len(numbers) < 3:
            logger.warning("Could not extract three amounts from summary")
            return None
        
        net_worth = normalize_money(numbers[0])
        vat = normalize_money(numbers[1])
        gross_worth = normalize_money(numbers[2])
        
        # Validate all fields are present
        if not all([seller_name, seller_tax_id, client_name, client_tax_id,
                   invoice_number, invoice_date, net_worth, vat, gross_worth]):
            logger.warning("Some fields missing after rule-based extraction")
            return None
        
        fields = {
            "Seller Name": seller_name,
            "Seller Tax ID": seller_tax_id,
            "Client Name": client_name,
            "Client Tax ID": client_tax_id,
            "Invoice Number": invoice_number,
            "Invoice Date": invoice_date,
            "Net Worth": net_worth,
            "VAT": vat,
            "Gross Worth": gross_worth,
        }
        
        extraction_time = (time.time() - start_time) * 1000
        
        return ExtractionResult(
            file_name="",  # Set by caller
            fields=fields,
            method=ExtractionMethod.OCR_RULES,
            extraction_time_ms=extraction_time,
        )
    
    except Exception as exc:
        logger.error("Rule-based extraction failed", exc_info=True)
        return None


# ============================================================================
# Main Extraction Pipeline
# ============================================================================

def extract_invoice(
    image_path: Path,
    sidecar: Optional[dict[str, str]],
    config: PipelineConfig,
) -> ExtractionResult:
    """
    Extract fields from invoice using hybrid approach.
    
    Tier-1: Document model (if enabled)
    Tier-2: LLM vision (if enabled)
    Tier-3: OCR + rules (always available as fallback)
    
    Args:
        image_path: Path to invoice image
        sidecar: Precomputed data if available
        config: Pipeline configuration
        
    Returns:
        ExtractionResult with fields and extraction method
        
    Raises:
        RuntimeError: If all extraction methods fail
    """
    start_time = time.time()
    
    logger.info(f"Starting extraction: {image_path.name}")
    
    # Tier 1: Document Model
    result = extract_with_document_model(image_path, config)
    if result:
        logger.info(
            f"Extraction successful via document model: {image_path.name}",
            time_ms=result.extraction_time_ms
        )
        return result
    
    # Tier 2: LLM Vision
    result = extract_with_llm_vision(image_path, config)
    if result:
        logger.info(
            f"Extraction successful via LLM vision: {image_path.name}",
            time_ms=result.extraction_time_ms
        )
        return result
    
    # Tier 3: OCR + Rules
    text = resolve_text(image_path, sidecar, config)
    if not text:
        logger.error(
            f"Could not extract text from {image_path.name}",
            available_methods="document_model, llm_vision, ocr"
        )
        raise RuntimeError(f"OCR extraction failed for {image_path.name}")
    
    payload = resolve_payload(sidecar)
    result = extract_with_rules(text, payload)
    
    if result:
        result.file_name = image_path.name
        logger.info(
            f"Extraction successful via OCR+rules: {image_path.name}",
            time_ms=result.extraction_time_ms
        )
        return result
    
    raise RuntimeError(f"All extraction methods failed for {image_path.name}")


def process_batch(
    image_paths: list[Path],
    sidecars: dict[str, dict[str, str]],
    config: PipelineConfig,
) -> tuple[list[ExtractionResult], list[tuple[Path, Exception]]]:
    """
    Process multiple invoices in parallel.
    
    Args:
        image_paths: List of image file paths
        sidecars: Sidecar data dictionary
        config: Pipeline configuration
        
    Returns:
        Tuple of (successful results, failed documents with errors)
    """
    results = []
    failures = []
    
    logger.info(
        f"Starting batch processing: {len(image_paths)} invoices",
        max_workers=config.max_workers
    )
    
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        # Submit all tasks
        futures = {
            executor.submit(
                extract_invoice,
                image_path,
                sidecars.get(image_path.name),
                config,
            ): image_path
            for image_path in image_paths
        }
        
        # Collect results as they complete
        completed = 0
        for future in as_completed(futures):
            image_path = futures[future]
            try:
                result = future.result(timeout=config.timeout_per_invoice)
                results.append(result)
                completed += 1
                
                if completed % 10 == 0:
                    logger.info(f"Processed {completed}/{len(image_paths)} invoices")
            
            except Exception as exc:
                logger.error(f"Extraction failed for {image_path.name}", exc_info=True)
                failures.append((image_path, exc))
    
    logger.info(
        f"Batch processing complete",
        total=len(image_paths),
        successful=len(results),
        failed=len(failures)
    )
    
    return results, failures


def save_results(
    results: list[ExtractionResult],
    output_path: Path,
    config: PipelineConfig,
) -> None:
    """
    Save extraction results to CSV and generate metrics report.
    
    Args:
        results: List of extraction results
        output_path: Path to output CSV file
        config: Pipeline configuration
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save CSV
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        
        for result in results:
            row = {**result.fields}
            writer.writerow(row)
    
    logger.info(f"Results saved to {output_path}")
    
    # Generate and save metrics
    metrics = {
        "total_invoices": len(results),
        "extraction_methods": {},
        "average_time_ms": 0,
        "total_time_ms": 0,
    }
    
    for method in ExtractionMethod:
        count = sum(1 for r in results if r.method == method)
        metrics["extraction_methods"][method.value] = count
    
    total_time = sum(r.extraction_time_ms for r in results)
    metrics["total_time_ms"] = total_time
    metrics["average_time_ms"] = total_time / len(results) if results else 0
    
    metrics_path = output_path.parent / "metrics.json"
    with metrics_path.open("w") as f:
        json.dump(metrics, f, indent=2)
    
    logger.info(f"Metrics saved to {metrics_path}")


# ============================================================================
# CLI and Main Entry Point
# ============================================================================

def main() -> int:
    """Main entry point for extraction pipeline."""
    parser = argparse.ArgumentParser(
        description="Production-ready hybrid invoice field extraction"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="Input directory (overrides config)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (overrides config)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        help="Invoices per batch (overrides config)"
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        help="Maximum parallel workers (overrides config)"
    )
    
    args = parser.parse_args()
    
    # Load configuration
    config = load_config()
    
    # Override with CLI arguments
    if args.input_dir:
        config.input_dir = args.input_dir
    if args.output_dir:
        config.output_dir = args.output_dir
    if args.batch_size:
        config.batch_size = args.batch_size
    if args.max_workers:
        config.max_workers = args.max_workers
    
    # Validate input directory
    if not config.input_dir.exists():
        logger.error(f"Input directory not found: {config.input_dir}")
        return 1
    
    logger.info(
        f"Starting extraction pipeline",
        environment=config.environment.value,
        input_dir=str(config.input_dir),
        output_dir=str(config.output_dir),
    )
    
    # Load sidecar data
    sidecars = load_sidecars(config.input_dir)
    
    # Find image files
    image_paths = sorted(config.input_dir.rglob("batch1-*.jpg"))
    logger.info(f"Found {len(image_paths)} invoice images")
    
    if not image_paths:
        logger.error("No invoice images found")
        return 1
    
    # Process batch
    results, failures = process_batch(image_paths, sidecars, config)
    
    # Save results
    output_csv = config.output_dir / "output.csv"
    save_results(results, output_csv, config)
    
    # Report summary
    logger.info(
        f"Extraction pipeline complete",
        successful=len(results),
        failed=len(failures),
        output_file=str(output_csv)
    )
    
    if failures:
        logger.warning(f"Failed to extract {len(failures)} invoices")
        for path, exc in failures[:5]:  # Log first 5 failures
            logger.warning(f"  - {path.name}: {str(exc)}")
    
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
