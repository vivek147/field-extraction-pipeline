"""
Quick script to extract invoice fields from a specific image range.

Limits extraction to a maximum of 50 images per run (configurable).

Usage:
    # Default: extract batch1-0331 to batch1-0380 (50 images)
    python extract_range.py
    
    # Custom range (must be <= 50 images)
    python extract_range.py --start 331 --end 350 --output output.csv
    
    # Change max image limit
    python extract_range.py --start 331 --end 340 --max-images 50 --output output.csv

Output:
    - output_range.csv (default) with extracted fields
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import Optional

# Import from main extraction module
from extract_production import (
    load_config,
    load_sidecars,
    extract_invoice,
    filter_images_by_range,
    logger,
    ExtractionMethod,
    OUTPUT_COLUMNS,
)


def extract_range(
    start_num: int,
    end_num: int,
    max_images: int = 50,
    input_dir: Optional[Path] = None,
    output_csv: Optional[Path] = None,
) -> int:
    """
    Extract invoice fields from a specific image range.
    
    Args:
        start_num: Start image number (e.g., 331 for batch1-0331.jpg)
        end_num: End image number (e.g., 380 for batch1-0380.jpg)
        max_images: Maximum images to process (default: 50, cannot exceed)
        input_dir: Input directory (defaults to batch_1)
        output_csv: Output CSV path (defaults to output_range.csv)
        
    Returns:
        Exit code (0 = success, 1 = failure)
        
    Raises:
        ValueError: If range validation fails
    """
    # Validate input
    if start_num < 0 or end_num < 0:
        logger.error("Image numbers cannot be negative")
        return 1
    
    if start_num > end_num:
        logger.error(f"Start image ({start_num}) cannot be greater than end image ({end_num})")
        return 1
    
    # Calculate expected image count
    expected_count = end_num - start_num + 1
    if expected_count > max_images:
        logger.error(
            f"Image range ({start_num}-{end_num}) would produce {expected_count} images, "
            f"exceeding max_images limit of {max_images}. "
            f"Please adjust range or increase max_images."
        )
        print(f"\n{'='*60}")
        print(f"ERROR: Image range exceeds maximum limit")
        print(f"{'='*60}")
        print(f"Requested range:    batch1-{start_num:04d} to batch1-{end_num:04d}")
        print(f"Expected images:    {expected_count}")
        print(f"Maximum allowed:    {max_images}")
        print(f"{'='*60}\n")
        return 1
    
    # Set defaults
    if input_dir is None:
        input_dir = Path("batch_1")
    if output_csv is None:
        output_csv = Path("output_range.csv")
    
    # Load configuration
    config = load_config()
    config.input_dir = input_dir
    config.output_dir = output_csv.parent
    
    logger.info(
        f"Starting extraction for image range",
        start=start_num,
        end=end_num,
        max_images=max_images,
        expected_count=expected_count,
        input_dir=str(input_dir),
        output_csv=str(output_csv),
    )
    
    # Load sidecar data
    sidecars = load_sidecars(config.input_dir)
    logger.info(f"Loaded {len(sidecars)} sidecar entries")
    
    # Find image files
    image_paths = sorted(config.input_dir.rglob("batch1-*.jpg"))
    logger.info(f"Found {len(image_paths)} total invoice images")
    
    # Apply range filter
    filtered_paths = filter_images_by_range(image_paths, start_num, end_num, max_images)
    logger.info(
        f"Filtered to {len(filtered_paths)} images in range {start_num}-{end_num}"
    )
    
    if not filtered_paths:
        logger.error(f"No images found in range {start_num}-{end_num}")
        return 1
    
    # Extract from each image
    results = []
    failures = []
    
    for idx, image_path in enumerate(filtered_paths, 1):
        try:
            sidecar = sidecars.get(image_path.name)
            result = extract_invoice(image_path, sidecar, config)
            results.append(result)
            
            if idx % 10 == 0:
                logger.info(f"Processed {idx}/{len(filtered_paths)} images")
        
        except Exception as exc:
            logger.error(
                f"Extraction failed for {image_path.name}: {str(exc)}",
                exc_info=False
            )
            failures.append((image_path, exc))
    
    # Save results to CSV
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        
        for result in results:
            row = {col: result.fields.get(col, "") for col in OUTPUT_COLUMNS}
            writer.writerow(row)
    
    # Report results
    logger.info(
        f"Extraction complete",
        total=len(filtered_paths),
        successful=len(results),
        failed=len(failures),
        output_file=str(output_csv),
    )
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"EXTRACTION SUMMARY")
    print(f"{'='*60}")
    print(f"Image range:        batch1-{start_num:04d} to batch1-{end_num:04d}")
    print(f"Maximum allowed:    {max_images} images")
    print(f"Total images:       {len(filtered_paths)}")
    print(f"Successful:         {len(results)}")
    print(f"Failed:             {len(failures)}")
    print(f"Success rate:       {len(results)/len(filtered_paths)*100:.1f}%")
    print(f"Output file:        {output_csv}")
    print(f"{'='*60}\n")
    
    # Show extraction methods used
    method_counts = {}
    for result in results:
        method = result.method.value
        method_counts[method] = method_counts.get(method, 0) + 1
    
    if method_counts:
        print("Extraction methods used:")
        for method, count in sorted(method_counts.items()):
            print(f"  - {method}: {count}")
        print()
    
    # Show first few failures
    if failures:
        print("First failures:")
        for path, exc in failures[:5]:
            print(f"  - {path.name}: {str(exc)}")
        print()
    
    return 0 if not failures else 1


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Extract invoice fields from a specific image range (max 50 images)"
    )
    parser.add_argument(
        "--start",
        type=int,
        default=331,
        help="Start image number (default: 331)"
    )
    parser.add_argument(
        "--end",
        type=int,
        default=380,
        help="End image number (default: 380)"
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=50,
        help="Maximum images to process (default: 50, enforced limit)"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Input directory (default: batch_1)"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV file (default: output_range.csv)"
    )
    
    args = parser.parse_args()
    
    return extract_range(
        start_num=args.start,
        end_num=args.end,
        max_images=args.max_images,
        input_dir=args.input_dir,
        output_csv=args.output,
    )


if __name__ == "__main__":
    sys.exit(main())
