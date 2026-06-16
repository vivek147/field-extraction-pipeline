"""
API wrapper for invoice extraction service.

Enables REST API access to extraction pipeline for microservice deployments.
Supports batch processing via job queue.
"""

from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict, List
import uuid
import os
import json
from pathlib import Path
from datetime import datetime
import logging

from extract_production import (
    extract_invoice,
    load_sidecars,
    ExtractionResult,
    ExtractionMethod,
)
from config import load_config
from logger import StructuredLogger

# Initialize FastAPI app
app = FastAPI(
    title="Invoice Extraction API",
    description="REST API for extracting fields from invoice images",
    version="1.0.0",
)

# Global logger
logger = StructuredLogger("api").get_logger()

# In-memory job tracking (use Redis/database for production)
jobs: Dict[str, Dict] = {}

# Configuration
config = load_config()
sidecars = load_sidecars(config.input_dir)


# ===== Request/Response Models =====

class ExtractionResponse(BaseModel):
    """Response model for extraction results."""

    job_id: str
    status: str  # pending, processing, success, failed
    fields: Optional[Dict] = None
    method: Optional[str] = None
    confidence: Optional[float] = None
    error: Optional[str] = None
    processing_time_ms: Optional[float] = None


class BatchRequest(BaseModel):
    """Request model for batch extraction."""

    file_paths: List[str]  # Paths to invoice images


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    models_available: Dict[str, bool]
    timestamp: str


# ===== Health Check =====


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.

    Returns system status, available models, and API version.
    """
    logger.info("health_check", endpoint="/health")

    models_available = {
        "document_model": bool(config.document_model.model_name),
        "llm_vision": bool(config.llm_vision.endpoint),
        "ocr": bool(config.ocr.engine),
    }

    return HealthResponse(
        status="healthy",
        version="1.0.0",
        models_available=models_available,
        timestamp=datetime.now().isoformat(),
    )


# ===== Single Invoice Extraction =====


@app.post("/extract", response_model=ExtractionResponse)
async def extract_single(file: UploadFile = File(...)):
    """
    Extract fields from a single invoice image.

    **Parameters:**
    - file: Invoice image file (JPEG, PNG)

    **Returns:**
    - Extracted fields, method used, confidence score

    **Example:**
    ```bash
    curl -X POST -F "file=@invoice.jpg" http://localhost:8000/extract
    ```
    """
    job_id = str(uuid.uuid4())

    try:
        # Save uploaded file temporarily
        temp_path = f"/tmp/{job_id}_{file.filename}"
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)

        logger.info(
            "extraction_started",
            job_id=job_id,
            file_name=file.filename,
            file_size=len(content),
        )

        # Extract fields
        start_time = datetime.now()
        result = extract_invoice(
            image_path=Path(temp_path),
            sidecar_data=sidecars.get(Path(temp_path).stem),
            config=config,
        )
        processing_time_ms = (datetime.now() - start_time).total_seconds() * 1000

        logger.info(
            "extraction_completed",
            job_id=job_id,
            method=result.method.value,
            time_ms=processing_time_ms,
        )

        # Clean up
        os.remove(temp_path)

        return ExtractionResponse(
            job_id=job_id,
            status="success",
            fields=result.fields,
            method=result.method.value,
            confidence=result.confidence,
            processing_time_ms=processing_time_ms,
        )

    except Exception as e:
        logger.error("extraction_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")


# ===== Batch Extraction (Async) =====


@app.post("/batch")
async def start_batch(request: BatchRequest, background_tasks: BackgroundTasks):
    """
    Start batch extraction job (asynchronous).

    **Parameters:**
    - file_paths: List of paths to invoice images

    **Returns:**
    - job_id: Use to poll status via /jobs/{job_id}

    **Example:**
    ```bash
    curl -X POST -H "Content-Type: application/json" \
      -d '{"file_paths": ["/path/to/invoice1.jpg", "/path/to/invoice2.jpg"]}' \
      http://localhost:8000/batch
    ```
    """
    job_id = str(uuid.uuid4())

    # Initialize job tracking
    jobs[job_id] = {
        "status": "pending",
        "total_files": len(request.file_paths),
        "processed": 0,
        "results": [],
        "errors": [],
        "started_at": datetime.now().isoformat(),
    }

    logger.info(
        "batch_job_created",
        job_id=job_id,
        total_files=len(request.file_paths),
    )

    # Process in background
    background_tasks.add_task(process_batch_job, job_id, request.file_paths)

    return {
        "job_id": job_id,
        "status": "pending",
        "message": f"Batch job {job_id} queued for processing",
    }


async def process_batch_job(job_id: str, file_paths: List[str]):
    """Process batch job in background."""
    jobs[job_id]["status"] = "processing"

    try:
        for i, file_path in enumerate(file_paths):
            try:
                result = extract_invoice(
                    image_path=Path(file_path),
                    sidecar_data=sidecars.get(Path(file_path).stem),
                    config=config,
                )

                jobs[job_id]["results"].append(
                    {
                        "file": file_path,
                        "fields": result.fields,
                        "method": result.method.value,
                    }
                )

            except Exception as e:
                jobs[job_id]["errors"].append(
                    {"file": file_path, "error": str(e)}
                )

                logger.error(
                    "batch_file_failed",
                    job_id=job_id,
                    file=file_path,
                    error=str(e),
                )

            jobs[job_id]["processed"] = i + 1

        jobs[job_id]["status"] = "success"
        jobs[job_id]["completed_at"] = datetime.now().isoformat()

        logger.info(
            "batch_job_completed",
            job_id=job_id,
            total=len(file_paths),
            successful=len(jobs[job_id]["results"]),
            failed=len(jobs[job_id]["errors"]),
        )

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        logger.error("batch_job_failed", job_id=job_id, error=str(e))


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """
    Get batch job status and results.

    **Parameters:**
    - job_id: Job ID from /batch response

    **Returns:**
    - Job status, progress, and results when complete

    **Example:**
    ```bash
    curl http://localhost:8000/jobs/550e8400-e29b-41d4-a716-446655440000
    ```
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return jobs[job_id]


# ===== Configuration Endpoint =====


@app.get("/config")
async def get_configuration():
    """
    Get current configuration.

    Returns active settings (document model, LLM endpoint, OCR engine, etc.).
    """
    return {
        "environment": config.environment.value,
        "document_model": {
            "enabled": bool(config.document_model.model_name),
            "model": config.document_model.model_name,
            "device": config.document_model.device,
        },
        "llm_vision": {
            "enabled": bool(config.llm_vision.endpoint),
            "endpoint": config.llm_vision.endpoint,
            "model": config.llm_vision.model_name,
            "timeout_seconds": config.llm_vision.timeout_seconds,
        },
        "ocr": {
            "engine": config.ocr.engine,
            "language": config.ocr.language,
        },
        "batch_processing": {
            "batch_size": config.batch_size,
            "max_workers": config.max_workers,
            "timeout_per_invoice": config.timeout_per_invoice,
        },
    }


# ===== Metrics Endpoint =====


@app.get("/metrics")
async def get_metrics():
    """
    Get extraction metrics (success rate, average time, method distribution).

    Reads from metrics.json generated by batch processing.
    """
    metrics_path = config.output_dir / "metrics.json"

    if not metrics_path.exists():
        return {"message": "No metrics available yet"}

    with open(metrics_path, "r") as f:
        return json.load(f)


# ===== Root Endpoint =====


@app.get("/")
async def root():
    """API documentation root."""
    return {
        "name": "Invoice Extraction API",
        "version": "1.0.0",
        "docs": "/docs",  # Swagger UI
        "endpoints": {
            "health": "GET /health",
            "extract_single": "POST /extract (file upload)",
            "batch_start": "POST /batch (async)",
            "job_status": "GET /jobs/{job_id}",
            "config": "GET /config",
            "metrics": "GET /metrics",
        },
    }


# ===== Error Handlers =====


@app.exception_handler(Exception)
async def exception_handler(exc: Exception):
    """Global exception handler."""
    logger.error("unhandled_exception", error=str(exc), exc_type=type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )


# ===== Startup/Shutdown =====


@app.on_event("startup")
async def startup():
    """Initialize on API startup."""
    logger.info("api_startup", environment=config.environment.value)


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on API shutdown."""
    logger.info("api_shutdown")


# ===== Local Testing =====

if __name__ == "__main__":
    import uvicorn

    # Run with: python api.py
    # Access: http://localhost:8000/docs (Swagger UI)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
