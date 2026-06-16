# Invoice Field Extraction

hybrid solution for extracting key fields from invoice images using OCR, LLMs, and document models.

## Architecture

**Three-tier extraction strategy** (graceful degradation):

```
┌─────────────────────────────────────────┐
│  Tier 1: Document Model (LayoutLM)      │ ← Best accuracy, structured invoices
│  Understands layout & semantics         │
└─────────────────────────┬───────────────┘
                          │ (if available)
                    ┌─────▼──────────┐
                    │ Tier 2: LLM     │
                    │ Vision (Claude) │ ← Handles messy/complex layouts
                    └─────┬──────────┘
                          │ (if available)
                    ┌─────▼──────────────────┐
                    │ Tier 3: OCR + Rules    │
                    │ (Always available)     │ ← Text-only fallback
                    └────────────────────────┘
```

##  Required Fields

The system extracts these 9 key fields from invoices:

- **Seller Name** - Company/vendor name
- **Seller Tax ID** - Tax registration number (format: XXX-XX-XXXX)
- **Client Name** - Customer/buyer name
- **Client Tax ID** - Customer tax number (format: XXX-XX-XXXX)
- **Invoice Number** - Unique invoice identifier
- **Invoice Date** - Issue date (MM/DD/YYYY)
- **Net Worth** - Subtotal before tax
- **VAT** - Tax amount
- **Gross Worth** - Total including tax

## Quick Start (Demo)

### 1. Installation

```bash
# Clone repository
cd E:\Code\poc

# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Run with Sample Data

```bash
# Using provided sidecar CSVs (fastest, ~10 seconds)
python extract_production.py

# Output:
# ✓ Extracted 1414 invoices to E:\Code\poc\output\output.csv
```

### 3. Verify Results

```bash
# Check output CSV
head output/output.csv

# View metrics
cat output/metrics.json
```

### 4. Demo Scenarios

**Scenario A: OCR + Rules Only (Always Works)**
```bash
# No models or LLM configured
# Falls back to regex pattern matching + sidecar data
python extract_production.py
# → ~1-2ms per invoice
```

**Scenario B: With Document Model (Higher Accuracy)**
```bash
# Enable LayoutLM for structured invoices
export DOCUMENT_MODEL=microsoft/layoutlm-base
export MODEL_DEVICE=cpu  # or cuda for GPU

python extract_production.py
# → ~50-100ms per invoice with CPU
# → ~20-30ms per invoice with GPU
```

**Scenario C: With LLM Vision (Handles Complex Layouts)**
```bash
# Use Claude 3.5 Sonnet for vision extraction
export LLM_VISION_ENDPOINT=https://api.anthropic.com/v1/messages
export LLM_API_KEY=sk-ant-...
export LLM_MODEL=claude-3-5-sonnet-20241022

python extract_production.py
# → ~2-3 seconds per invoice (includes API latency)
# → Handles handwritten, rotated, poor-quality invoices
```

**Scenario D: Full Hybrid (Document Model → LLM → OCR)**
```bash
# All tiers enabled
export DOCUMENT_MODEL=microsoft/layoutlm-base
export LLM_VISION_ENDPOINT=https://api.anthropic.com/v1/messages
export LLM_API_KEY=sk-ant-...

python extract_production.py
# → Optimal accuracy for diverse invoice types
# → Automatic fallback if primary methods fail
```

##  Performance Metrics

Output: `output/metrics.json`

```json
{
  "total_invoices": 1414,
  "extraction_methods": {
    "document_model": 0,
    "llm_vision": 0,
    "ocr_rules": 1414,
    "sidecar_json": 0
  },
  "average_time_ms": 0.85,
  "total_time_ms": 1203.5
}
```

## Configuration

All settings via environment variables:

```bash
# Environment
export INVOICE_ENV=production  # development|staging|production

# Paths
export INVOICE_INPUT_DIR=/data/invoices
export INVOICE_OUTPUT_DIR=/data/output

# Batch Processing
export BATCH_SIZE=10
export MAX_WORKERS=4
export TIMEOUT_PER_INVOICE=300

# Tier 1: Document Model
export DOCUMENT_MODEL=microsoft/layoutlm-base
export MODEL_DEVICE=cuda  # cpu|cuda

# Tier 2: LLM Vision
export LLM_VISION_ENDPOINT=https://api.anthropic.com/v1/messages
export LLM_API_KEY=sk-ant-...
export LLM_MODEL=claude-3-5-sonnet-20241022
export LLM_TIMEOUT=60

# Tier 3: OCR
export OCR_ENGINE=tesseract  # tesseract|paddleocr
export OCR_LANGUAGE=eng

# Logging
export LOG_LEVEL=INFO
export LOG_FILE=logs/extraction.log
```

##  Deployment Guide

### Option 1: Docker 

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for OCR
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy code
COPY . .

# Install Python dependencies
RUN pip install -r requirements.txt

# Create output directory
RUN mkdir -p logs

# Run extraction
CMD ["python", "extract_production.py"]
```

Build and run:
```bash
# Build image
docker build -t invoice-extractor:1.0 .

# Run with sidecar data (fast)
docker run \
  -v /path/to/invoices:/data/invoices \
  -v /path/to/output:/data/output \
  -e INVOICE_INPUT_DIR=/data/invoices \
  -e INVOICE_OUTPUT_DIR=/data/output \
  invoice-extractor:1.0

# Run with LLM vision
docker run \
  -v /path/to/invoices:/data/invoices \
  -v /path/to/output:/data/output \
  -e LLM_VISION_ENDPOINT=https://api.anthropic.com/v1/messages \
  -e LLM_API_KEY=sk-ant-... \
  -e INVOICE_ENV=production \
  invoice-extractor:1.0
```

### Option 2: Cloud Deployment (AWS Lambda + RDS)

```python
# lambda_handler.py
import json
from extract_production import extract_invoice, load_sidecars
from config import load_config

def lambda_handler(event, context):
    """AWS Lambda handler for invoice extraction."""
    config = load_config()
    
    # Get invoice path from event
    image_key = event["Records"][0]["s3"]["object"]["key"]
    
    # Download from S3
    import boto3
    s3 = boto3.client("s3")
    s3.download_file(
        event["Records"][0]["s3"]["bucket"]["name"],
        image_key,
        f"/tmp/{image_key.split('/')[-1]}"
    )
    
    # Extract
    sidecars = load_sidecars(config.input_dir)
    result = extract_invoice(Path(f"/tmp/{image_key}"), sidecars.get(...), config)
    
    # Save to RDS/DynamoDB
    db.insert_extraction_result(result)
    
    return {
        "statusCode": 200,
        "body": json.dumps(result.fields)
    }
```

Deployment:
```bash
# Package for Lambda
pip install -r requirements.txt -t package/
cd package && zip -r ../deployment.zip . && cd ..
zip -g deployment.zip lambda_handler.py extract_production.py config.py logger.py

# Upload to Lambda
aws lambda update-function-code \
  --function-name invoice-extractor \
  --zip-file fileb://deployment.zip
```


##  Testing

```bash
# Unit tests
pytest tests/ -v

# Integration tests (with sample invoices)
pytest tests/integration/ -v

# Performance benchmarks
python benchmarks/performance.py
# Output: OCR+rules: 0.85ms, Document Model: 50ms, LLM Vision: 2500ms

# Load testing (1K concurrent invoices)
locust -f tests/load.py --headless -u 1000 -r 100 -t 60s
```

##  Monitoring & Observability

### Logging

```bash
# Real-time logs
tail -f logs/extraction.log

# Parse metrics
grep "extraction successful" logs/extraction.log | wc -l
```

### Dashboards (Grafana)

```json
{
  "dashboard": {
    "title": "Invoice Extraction",
    "panels": [
      {
        "title": "Extraction Success Rate",
        "targets": [{"expr": "successful_extractions / total_invoices"}]
      },
      {
        "title": "Average Extraction Time",
        "targets": [{"expr": "avg(extraction_time_ms)"}]
      },
      {
        "title": "Extraction Method Distribution",
        "targets": [{"expr": "rate(extractions_by_method[5m])"}]
      }
    ]
  }
}
```
