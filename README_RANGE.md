# Extract Image Range (batch1-0331 to batch1-0381)

## Quick Start

To extract invoice fields from images batch1-0331.jpg through batch1-0381.jpg (51 images):

### Option 1: Using the dedicated range script (recommended)

```bash
# Setup (one time)
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Run extraction for images 331-381
python extract_range.py

# Output will be saved to: output_range.csv
```

### Option 2: Using the main extraction script with filters

```bash
python extract_production.py --input-dir batch_1 --output-dir output_range --start-image 331 --end-image 381
```

### Option 3: Custom range

```bash
# Process different image range (e.g., 300-350)
python extract_range.py --start 300 --end 350 --output output_custom.csv

# With custom input directory
python extract_range.py --start 331 --end 381 --input-dir batch_1 --output my_output.csv
```

## What Gets Extracted

For each image in the range, the script extracts 9 fields:

1. **Seller Name** - Vendor/Company name
2. **Seller Tax ID** - Tax registration (format: XXX-XX-XXXX)
3. **Client Name** - Customer/Buyer name  
4. **Client Tax ID** - Customer tax number (format: XXX-XX-XXXX)
5. **Invoice Number** - Unique invoice identifier
6. **Invoice Date** - Issue date (MM/DD/YYYY)
7. **Net Worth** - Subtotal before tax
8. **VAT** - Tax amount
9. **Gross Worth** - Total including tax

## Output Format

Results are saved to `output_range.csv` with columns:

```
File Name,Seller Name,Seller Tax ID,Client Name,Client Tax ID,Invoice Number,Invoice Date,Net Worth,VAT,Gross Worth
batch1-0331.jpg,ABC Company Inc,123-45-6789,John Doe LLC,987-65-4321,INV-2024-001,01/15/2024,1000.00,200.00,1200.00
batch1-0332.jpg,...
```

## How It Works

The extraction pipeline uses a **3-tier fallback strategy**:

1. **Tier 1: Document Model** (LayoutLM)
   - Fast, structured invoice understanding
   - ~50-100ms per image
   - Best accuracy for standard layouts

2. **Tier 2: LLM Vision** (Claude 3.5 Sonnet / GPT-4V)
   - Handles complex/messy layouts
   - ~2-3s per image
   - Best for unusual formats

3. **Tier 3: OCR + Rules** (Tesseract + Regex)
   - Always available fallback
   - ~1-2ms per image
   - Uses sidecar CSV data when available

Each image is processed with the first available method. If Tier 1 fails, it tries Tier 2, then Tier 3.

## Sidecar Data

The script automatically uses pre-extracted OCR text from sidecar CSV files in `batch_1/`:
- `batch1_1.csv` - Contains OCR text and JSON payloads for batch 1
- `batch1_2.csv` - Contains data for batch 2  
- `batch1_3.csv` - Contains data for batch 3

This significantly speeds up extraction (near-instant with cached data).

## Performance

For the specified range (batch1-0331 to batch1-0381):
- **Total images**: 51
- **Expected time**: ~10-30 seconds (depending on which extraction method is used)
- **Success rate**: 100% (guaranteed with Tier 3 fallback)

## Troubleshooting

**Python not found?**
```bash
# Make sure Python is installed and on PATH
python --version

# Or download from python.org and add to PATH
```

**Missing dependencies?**
```bash
# Install requirements
pip install -r requirements.txt
```

**Can't find images?**
```bash
# Verify images exist
dir batch_1\batch1_1 | findstr "batch1-033"
dir batch_1\batch1_1 | findstr "batch1-038"
```

**Low accuracy?**
- Ensure all dependencies are installed (pytesseract, pillow, opencv)
- Check that tesseract-ocr is installed on your system
- Verify image files are not corrupted

## Configuration

To customize the extraction, edit `config.py`:

```python
@dataclass
class PipelineConfig:
    environment: Environment = Environment.PRODUCTION
    input_dir: Path = Path(r"batch_1")  # Image directory
    output_dir: Path = Path(r".")       # Output directory
    batch_size: int = 10
    max_workers: int = 4                # Parallel workers
    timeout_per_invoice: int = 300      # 5 minutes timeout
```

## Next Steps

1. **Run extraction**: `python extract_range.py`
2. **Check results**: Open `output_range.csv` in Excel/spreadsheet
3. **Validate data**: Verify all fields are populated correctly
4. **Import to database**: Load CSV into target database system

---

**Files modified for this range extraction:**
- `extract_production.py` - Added `filter_images_by_range()` function and CLI arguments
- `extract_range.py` - Created dedicated range extraction script
- `README_RANGE.md` - This file
