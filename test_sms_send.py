import os
import logging
from dotenv import load_dotenv
from sms_booking import send_sms

# Load environment variables from .env file
load_dotenv()

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Test sending an SMS
to_number = "+19546144683"  # Test number to send to
message = "This is a test message from the GoldTouch system. Please ignore."

# Print environment variables for debugging
print("=== Environment Variables ===")
print(f"TEXTMAGIC_USERNAME: {'Set' if os.getenv('TEXTMAGIC_USERNAME') else 'Not set'}")
print(f"TEXTMAGIC_API_KEY: {'Set' if os.getenv('TEXTMAGIC_API_KEY') else 'Not set'}")
print(f"TEXTMAGIC_FROM: {os.getenv('TEXTMAGIC_FROM', 'Not set')}")

print(f"\nSending test message to {to_number}...")
success, result = send_sms(to=to_number, body=message)
print(f"\n=== Test Result ===")
print(f"Success: {success}")
print(f"Result: {result}")
