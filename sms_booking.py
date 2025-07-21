import os
import time
import logging
from threading import Timer
from load_providers import load_providers
import requests

# Set up logging
logging.basicConfig(level=logging.INFO)

# ClickSend SMS config
CLICKSEND_USERNAME = os.getenv('CLICKSEND_USERNAME', 'YOUR_CLICKSEND_USERNAME')
CLICKSEND_API_KEY = os.getenv('CLICKSEND_API_KEY', 'YOUR_CLICKSEND_API_KEY')
CLICKSEND_FROM_NUMBER = os.getenv('CLICKSEND_FROM_NUMBER', 'GoldTouch')  # Can be alphanumeric or phone number
CLICKSEND_SMS_URL = "https://rest.clicksend.com/v3/sms/send"

# Helper to send SMS using ClickSend

def send_sms(to, body):
    """Send an SMS using ClickSend.
    
    Args:
        to (str): Recipient phone number in international format (e.g., '+1234567890')
        body (str): Message content
        
    Returns:
        bool: True if SMS was sent successfully, False otherwise
    """
    logger = logging.getLogger(__name__)
    logger.info(f"Attempting to send SMS to {to}: {body[:50]}...")
    
    try:
        payload = {
        "messages": [
            {
                "source": "python",
                "from": CLICKSEND_FROM_NUMBER,
                "body": body,
                "to": to
            }
        ]
    }
        resp = requests.post(
            CLICKSEND_SMS_URL,
            auth=(CLICKSEND_USERNAME, CLICKSEND_API_KEY),
            json=payload
        )
        
        success = resp.status_code == 200
        if success:
            logger.info(f"SMS sent successfully to {to}")
        else:
            logger.error(f"Failed to send SMS to {to}. Status: {resp.status_code}, Response: {resp.text}")
            
        return success
        
    except Exception as e:
        logger.error(f"Error sending SMS to {to}: {str(e)}")
        return False

# Booking logic class
class SMSBookingManager:
    def __init__(self, sheet_name='Massage Providers'):
        self.providers = load_providers(sheet_name)
        self.pending_requests = {}  # booking_id: timer

    def find_providers(self, location, massage_type, exclude=[]):
        # Simple filter logic, can be improved with geolocation
        filtered = [p for p in self.providers if p['Location'] == location and p['Type'] == massage_type and p['Phone'] not in exclude]
        return filtered

    def send_booking_request(self, booking_id, client_phone, location, massage_type, provider_phone):
        body = f"New booking request at {location} for {massage_type} massage. Are you available? Reply YES or NO."
        send_sms(provider_phone, body)
        # Start 15 min timer for provider response
        timer = Timer(900, self.handle_no_response, args=(booking_id, client_phone, location, massage_type, provider_phone))
        timer.start()
        self.pending_requests[booking_id] = timer

    def handle_provider_response(self, booking_id, provider_phone, response):
        # Cancel timer if exists
        timer = self.pending_requests.pop(booking_id, None)
        if timer:
            timer.cancel()
        if response.strip().upper() == "YES":
            # Confirm booking
            send_sms(provider_phone, "Thank you! Booking confirmed.")
            # Notify client (optional)
        else:
            # Try next provider
            pass  # Implement fallback logic

    def handle_no_response(self, booking_id, client_phone, location, massage_type, provider_phone):
        # Called if no response in 15 min
        send_sms(provider_phone, "Thanks for getting back to us — the job was sent to another provider since we didn’t hear back in time. We’ll reach out again for future bookings!")
        # Try next provider or notify client
        pass

# Example usage
if __name__ == "__main__":
    manager = SMSBookingManager()
    # Simulate booking
    booking_id = "abc123"
    client_phone = "+15550001111"
    location = "Downtown"
    massage_type = "Mobile"
    providers = manager.find_providers(location, massage_type)
    if providers:
        manager.send_booking_request(booking_id, client_phone, location, massage_type, providers[0]['Phone'])
    else:
        print("No providers found.")
