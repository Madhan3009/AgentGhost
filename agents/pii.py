"""
Ghost Requirement Agent — PII Masking Module
============================================
Provides functions to redact or hash PII (emails, phone numbers, IP addresses)
from raw message content before LLM submission to comply with compliance policies.
"""
import re
import hashlib
from agents.config import PII_SALT

# Regex patterns for matching common PII components
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_REGEX = re.compile(
    r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
)
IP_REGEX = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
    r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b|"
    r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
)

def hash_email(email: str) -> str:
    """Hash email using SHA-256 with a salt to protect identity while maintaining uniqueness."""
    salted = f"{email.lower()}:{PII_SALT}"
    hashed = hashlib.sha256(salted.encode("utf-8")).hexdigest()
    # Keep the domain visible for context (e.g., internal vs external user) but hash the mailbox portion
    domain = email.split("@")[-1] if "@" in email else "domain"
    return f"[EMAIL_HASHED_{hashed[:12]}@{domain}]"

def mask_pii_content(text: str) -> str:
    """
    Scrub raw text of email addresses (hashed), phone numbers (masked), and IP addresses (masked).
    
    Args:
        text: Raw user message
        
    Returns:
        PII-scrubbed text string
    """
    if not text:
        return text
        
    # 1. Mask Phone Numbers
    text = PHONE_REGEX.sub("[PHONE_MASKED]", text)
    
    # 2. Mask IP Addresses
    text = IP_REGEX.sub("[IP_MASKED]", text)
    
    # 3. Hash Email Addresses
    def replace_email(match):
        return hash_email(match.group(0))
        
    text = EMAIL_REGEX.sub(replace_email, text)
    
    return text
