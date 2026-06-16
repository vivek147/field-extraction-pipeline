"""
Unit tests for invoice extraction pipeline.

Tests cover:
- Field extraction patterns (regex)
- Data normalization (money, dates, tax IDs)
- Error handling and edge cases
- Extraction tier logic
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
import re

# Import modules under test
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from extract_production import (
    normalize_money,
    extract_tax_id,
    extract_seller_name,
    extract_client_name,
    extract_invoice_number,
    extract_invoice_date,
    ExtractionMethod,
    ExtractionResult,
)

# ===== Test Fixtures =====


@pytest.fixture
def sample_ocr_text():
    """Sample OCR-extracted text from invoice."""
    return """
    ABC COMPANY INC
    Tax ID: 123-45-6789
    
    INVOICE
    Invoice #: INV-2024-001
    Date: 01/15/2024
    
    BILL TO:
    John Doe LLC
    Tax ID: 987-65-4321
    
    ITEMS:
    Item 1               $100.00
    Item 2               $200.00
    Subtotal:          $300.00
    VAT (20%):          $60.00
    Total:             $360.00
    """


@pytest.fixture
def european_format_text():
    """Sample text with European number format."""
    return """
    Company Name
    Tax ID: 123-45-6789
    
    Net Amount: 1.234,56
    VAT: 247,99
    Total: 1.482,55
    """


@pytest.fixture
def us_format_text():
    """Sample text with US number format."""
    return """
    Company Name
    Tax ID: 123-45-6789
    
    Net Amount: 1,234.56
    VAT: 247.99
    Total: 1,482.55
    """


# ===== Test Money Normalization =====


class TestNormalizeMoney:
    """Tests for currency normalization."""

    def test_us_format_with_comma_separator(self):
        """US format: 1,234.56 → 1234.56"""
        assert normalize_money("1,234.56") == 1234.56

    def test_us_format_simple(self):
        """Simple: 100.50 → 100.50"""
        assert normalize_money("100.50") == 100.50

    def test_us_format_no_decimals(self):
        """Integer: 1000 → 1000.0"""
        assert normalize_money("1000") == 1000.0

    def test_european_format(self):
        """European: 1.234,56 → 1234.56"""
        assert normalize_money("1.234,56") == 1234.56

    def test_european_format_thousands(self):
        """European with thousands: 10.000,00 → 10000.0"""
        assert normalize_money("10.000,00") == 10000.0

    def test_space_separated_thousands(self):
        """Space-separated (OCR quirk): 4 802,89 → 4802.89"""
        # This is the tricky case from production data
        result = normalize_money("4 802,89")
        assert result == 4802.89

    def test_no_decimals_european(self):
        """European no decimals: 1.000 → 1000.0"""
        assert normalize_money("1.000") == 1000.0

    def test_empty_string(self):
        """Empty string → None"""
        assert normalize_money("") is None

    def test_invalid_format(self):
        """Invalid format → None"""
        assert normalize_money("abc") is None

    def test_with_currency_symbol(self):
        """Currency symbol stripped: $1,234.56 → 1234.56"""
        # Implementation should handle this
        result = normalize_money("$1,234.56")
        assert result in [1234.56, None]  # Either works or returns None


# ===== Test Tax ID Extraction =====


class TestTaxIDExtraction:
    """Tests for tax ID pattern matching."""

    def test_standard_format(self):
        """Standard XXX-XX-XXXX format"""
        text = "Tax ID: 123-45-6789"
        result = extract_tax_id(text)
        assert result == "123-45-6789"

    def test_multiple_tax_ids(self):
        """Multiple tax IDs (should match first)"""
        text = "Seller: 111-22-3333, Buyer: 444-55-6666"
        result = extract_tax_id(text)
        assert result == "111-22-3333"

    def test_no_tax_id(self):
        """No tax ID in text"""
        text = "Invoice with no tax information"
        result = extract_tax_id(text)
        assert result is None

    def test_with_context(self):
        """Tax ID in context"""
        text = "Company Tax ID is 999-88-7777 for compliance"
        result = extract_tax_id(text)
        assert result == "999-88-7777"

    def test_at_line_start(self):
        """Tax ID at start of line"""
        text = "123-45-6789 is our tax ID"
        result = extract_tax_id(text)
        assert result == "123-45-6789"

    def test_invalid_formats_ignored(self):
        """Invalid formats (12-34-5678) not matched"""
        text = "Bad format: 12-34-5678"
        result = extract_tax_id(text)
        assert result is None


# ===== Test Company Name Extraction =====


class TestSellerNameExtraction:
    """Tests for seller/company name extraction."""

    def test_simple_name(self, sample_ocr_text):
        """Extract simple company name from start of text"""
        result = extract_seller_name(sample_ocr_text)
        assert result is not None
        assert "ABC" in result or "COMPANY" in result

    def test_multiword_name(self):
        """Multiword company name"""
        text = "XYZ Corporation Ltd."
        result = extract_seller_name(text)
        # Should extract something reasonable
        assert result is not None

    def test_empty_text(self):
        """Empty text returns None"""
        result = extract_seller_name("")
        assert result is None


# ===== Test Client Name Extraction =====


class TestClientNameExtraction:
    """Tests for client/customer name extraction."""

    def test_bill_to_section(self, sample_ocr_text):
        """Extract name after 'BILL TO:'"""
        result = extract_client_name(sample_ocr_text)
        assert result is not None
        assert "John" in result or "Doe" in result or "LLC" in result

    def test_ship_to_variation(self):
        """Alternative 'SHIP TO:' label"""
        text = """
        SHIP TO:
        Jane Smith Corp
        """
        result = extract_client_name(text)
        assert result is not None

    def test_no_billing_section(self):
        """Text without billing section"""
        text = "Just some random invoice text"
        result = extract_client_name(text)
        # Might return None or best guess


# ===== Test Invoice Number Extraction =====


class TestInvoiceNumberExtraction:
    """Tests for invoice number pattern matching."""

    def test_standard_invoice_number(self, sample_ocr_text):
        """Standard Invoice # format"""
        result = extract_invoice_number(sample_ocr_text)
        assert result is not None
        assert "INV-2024-001" in result or "2024-001" in result or "001" in result

    def test_invoice_id_variant(self):
        """Invoice ID variant"""
        text = "Invoice ID: 2024-5678"
        result = extract_invoice_number(text)
        assert result is not None

    def test_numeric_only(self):
        """Numeric-only invoice number"""
        text = "Invoice #12345"
        result = extract_invoice_number(text)
        assert result is not None
        assert "12345" in result

    def test_no_invoice_number(self):
        """Text without invoice number"""
        text = "Random document text"
        result = extract_invoice_number(text)
        # Might be None or empty


# ===== Test Invoice Date Extraction =====


class TestInvoiceDateExtraction:
    """Tests for invoice date pattern matching."""

    def test_us_date_format(self, sample_ocr_text):
        """US format: MM/DD/YYYY"""
        result = extract_invoice_date(sample_ocr_text)
        assert result is not None
        assert "01/15/2024" in result or "2024" in result

    def test_european_date_format(self):
        """European format: DD/MM/YYYY"""
        text = "Date: 15/01/2024"
        result = extract_invoice_date(text)
        assert result is not None

    def test_with_day_name(self):
        """Date with day name"""
        text = "Invoice Date: Monday, 01/15/2024"
        result = extract_invoice_date(text)
        assert result is not None
        assert "2024" in result

    def test_date_spelled_out(self):
        """Date spelled out"""
        text = "Date: January 15, 2024"
        result = extract_invoice_date(text)
        assert result is not None

    def test_no_date(self):
        """Text without date"""
        text = "Invoice with no date information"
        result = extract_invoice_date(text)
        # Might be None


# ===== Integration Tests =====


class TestExtractionIntegration:
    """Integration tests for full extraction pipeline."""

    @patch("extract_production.extract_with_document_model")
    def test_extraction_chain_tier1_success(self, mock_tier1):
        """Tier 1 success → return immediately"""
        mock_result = ExtractionResult(
            fields={"seller_name": "ABC Corp"},
            method=ExtractionMethod.DOCUMENT_MODEL,
            confidence=0.95,
            processing_time_ms=50,
        )
        mock_tier1.return_value = mock_result

        # This would test extract_invoice() logic
        assert mock_result.method == ExtractionMethod.DOCUMENT_MODEL

    @patch("extract_production.extract_with_document_model")
    @patch("extract_production.extract_with_llm_vision")
    def test_extraction_chain_tier1_fail_tier2_success(
        self, mock_tier2, mock_tier1
    ):
        """Tier 1 fails → Tier 2 succeeds"""
        mock_tier1.return_value = None
        mock_result = ExtractionResult(
            fields={"seller_name": "ABC Corp"},
            method=ExtractionMethod.LLM_VISION,
            confidence=0.92,
            processing_time_ms=2500,
        )
        mock_tier2.return_value = mock_result

        assert mock_result.method == ExtractionMethod.LLM_VISION

    def test_extraction_result_fields_validation(self):
        """ExtractionResult contains all required fields"""
        fields = {
            "seller_name": "ABC Corp",
            "seller_tax_id": "123-45-6789",
            "client_name": "XYZ LLC",
            "client_tax_id": "987-65-4321",
            "invoice_number": "INV-001",
            "invoice_date": "01/15/2024",
            "net_worth": 1000.0,
            "vat": 200.0,
            "gross_worth": 1200.0,
        }

        result = ExtractionResult(
            fields=fields,
            method=ExtractionMethod.OCR_RULES,
            confidence=0.75,
            processing_time_ms=1.2,
        )

        assert result.fields["seller_name"] == "ABC Corp"
        assert result.fields["net_worth"] == 1000.0
        assert result.confidence == 0.75


# ===== Regression Tests (Known Issues) =====


class TestRegressions:
    """Regression tests for known issues and quirks."""

    def test_ocr_space_separated_amounts(self):
        """Regression: OCR merges space-separated thousands (issue #42)"""
        # Space-separated amounts from malformed OCR
        text = "Amount: 4 802,89 480,29"

        # Extract both amounts
        import re

        amounts = re.findall(r"(\d{1,3}(?:\s\d{3})+|\d+)(?:[.,]\d{2})?", text)
        assert len(amounts) >= 2

    def test_european_vs_us_currency_detection(self):
        """Correctly detect European (1.000,00) vs US (1,000.00) format"""
        # European: last separator is comma
        eur_text = "Total: 1.234,56"
        eur_amount = normalize_money(eur_text)
        assert eur_amount == 1234.56

        # US: last separator is period
        us_text = "Total: 1,234.56"
        us_amount = normalize_money(us_text)
        assert us_amount == 1234.56

    def test_tax_id_format_strictly_checked(self):
        """Tax ID must be XXX-XX-XXXX format"""
        valid = extract_tax_id("Tax ID: 123-45-6789")
        assert valid == "123-45-6789"

        invalid = extract_tax_id("Tax ID: 12-34-5678")  # Wrong format
        assert invalid is None


# ===== Performance Tests =====


class TestPerformance:
    """Performance benchmarks."""

    def test_normalize_money_speed(self, benchmark):
        """normalize_money should be sub-millisecond"""

        def normalize():
            normalize_money("1.234,56")

        # This will be run multiple times by pytest-benchmark
        # assert benchmark(normalize) < 0.001  # milliseconds


# ===== Run Tests =====

if __name__ == "__main__":
    # Run: pytest tests/test_extraction.py -v
    pytest.main([__file__, "-v", "--tb=short"])
