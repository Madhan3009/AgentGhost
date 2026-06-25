import pytest
from agents.pii import mask_pii_content, hash_email


def test_hash_email():
    email = "test@company.com"
    hashed = hash_email(email)
    
    # Hashed email should include the domain and the marker
    assert "[EMAIL_HASHED_" in hashed
    assert "@company.com]" in hashed
    
    # Same email should yield same hash
    assert hash_email(email) == hashed
    # Case insensitivity check
    assert hash_email("TEST@company.com") == hashed


def test_mask_pii_content():
    # 1. Emails
    text_email = "Contact support at mail@gmail.com for help."
    masked = mask_pii_content(text_email)
    assert "mail@gmail.com" not in masked
    assert "[EMAIL_HASHED_" in masked
    assert "@gmail.com]" in masked

    # 2. Phone numbers
    text_phone = "My number is +1 123-456-7890 or (123) 456-7890."
    masked_phone = mask_pii_content(text_phone)
    assert "123-456-7890" not in masked_phone
    assert "[PHONE_MASKED]" in masked_phone

    # 3. IP Addresses
    text_ip = "Server IP is 192.168.1.1 or 2001:0db8:85a3:0000:0000:8a2e:0370:7334."
    masked_ip = mask_pii_content(text_ip)
    assert "192.168.1.1" not in masked_ip
    assert "[IP_MASKED]" in masked_ip

    # 4. Mixed
    mixed = "Call me at 555-555-5555 or email john@doe.com, IP: 10.0.0.1"
    masked_mixed = mask_pii_content(mixed)
    assert "[PHONE_MASKED]" in masked_mixed
    assert "[EMAIL_HASHED_" in masked_mixed
    assert "@doe.com]" in masked_mixed
    assert "[IP_MASKED]" in masked_mixed
    assert "john@doe.com" not in masked_mixed
    assert "555-555-5555" not in masked_mixed
    assert "10.0.0.1" not in masked_mixed
