import os
import time
import logging
from threading import Timer
from load_providers import load_providers
import requests

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ClickSend SMS config
CLICKSEND_USERNAME = os.getenv('CLICKSEND_USERNAME', 'YOUR_CLICKSEND_USERNAME')
CLICKSEND_API_KEY = os.getenv('CLICKSEND_API_KEY', 'YOUR_CLICKSEND_API_KEY')
CLICKSEND_FROM_NUMBER = os.getenv('CLICKSEND_FROM_NUMBER', 'GoldTouch')  # Can be alphanumeric or phone number
CLICKSEND_SMS_URL = "https://rest.clicksend.com/v3/sms/send"

# Helper to send SMS using ClickSend

def send_sms(to, body, from_number=None):
    """Send an SMS using ClickSend.
    
    Args:
        to (str): Recipient phone number in international format (e.g., '+1234567890')
        body (str): Message content
        from_number (str, optional): Sender ID or number. Defaults to CLICKSEND_FROM_NUMBER.
        
    Returns:
        bool: True if SMS was sent successfully, False otherwise
    """
    logger = logging.getLogger(__name__)
    
    # Use provided from_number or fall back to CLICKSEND_FROM_NUMBER
    from_number = from_number or CLICKSEND_FROM_NUMBER
    
    # Basic phone number validation
    to = str(to).strip() if to else None
    from_number = str(from_number).strip() if from_number else None
    
    # Basic validation - must contain at least 10 digits
    if not to or sum(c.isdigit() for c in to) < 10:
        error_msg = f"Invalid 'to' number format. Must contain at least 10 digits. Got: {to}"
        logger.error(error_msg)
        return False, error_msg
        
    if not from_number or sum(c.isdigit() for c in from_number) < 10:
        error_msg = f"Invalid 'from' number format. Must contain at least 10 digits. Got: {from_number}"
        logger.error(error_msg)
        return False, error_msg
    
    logger.info(f"Preparing to send SMS - From: '{from_number}', To: '{to}', Body: '{body[:50]}...'")
    
    # Prevent sending to the same number (to avoid loops)
    if to == from_number:
        error_msg = f"Cannot send SMS: 'to' and 'from' numbers are the same: {to}"
        logger.error(error_msg)
        return False, error_msg
    
    # Validate phone numbers
    if not to.startswith('+'):
        error_msg = f"Invalid 'to' number format. Must start with '+'. Got: {to}"
        logger.error(error_msg)
        return False, error_msg
        
    if not (from_number.startswith('+') or from_number.isalpha()):
        error_msg = f"Invalid 'from' number format. Must be alphanumeric or start with '+'. Got: {from_number}"
        logger.error(error_msg)
        return False, error_msg
    
    logger.info(f"Sending SMS - From: '{from_number}', To: '{to}', Body length: {len(body)} chars")
    
    try:
        # Prepare the payload
        payload = {
            "messages": [
                {
                    "source": "python",
                    "from": from_number,
                    "body": body,
                    "to": to
                }
            ]
        }
        
        logger.info(f"Sending request to ClickSend - URL: {CLICKSEND_SMS_URL}")
        logger.debug(f"Request payload: {payload}")
        
        # Make the API request
        response = requests.post(
            CLICKSEND_SMS_URL,
            auth=(CLICKSEND_USERNAME, CLICKSEND_API_KEY),
            json=payload,
            timeout=10
        )
        
        logger.info(f"Received response - Status: {response.status_code}")
        logger.debug(f"Response: {response.text}")
        
        # Check for HTTP errors
        response.raise_for_status()
        
        # Check the response content
        response_data = response.json()
        if response_data.get('response_code') != 'SUCCESS':
            error_msg = f"ClickSend API error: {response_data.get('response_msg', 'Unknown error')}"
            logger.error(error_msg)
            return False, error_msg
            
        logger.info(f"SMS sent successfully to {to}")
        return True, "Message sent successfully"
        
    except requests.exceptions.RequestException as e:
        error_msg = f"Failed to send SMS: {str(e)}"
        logger.error(error_msg)
        
        # Log detailed error information if available
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response status: {e.response.status_code}")
            try:
                error_detail = e.response.json()
                logger.error(f"Error details: {error_detail}")
                error_msg = f"{error_msg} - {error_detail.get('response_msg', 'No details')}"
            except:
                logger.error(f"Response text: {e.response.text}")
                error_msg = f"{error_msg} - {e.response.text}"
                
        return False, error_msg

# Booking logic class
class SMSBookingManager:
    def __init__(self, sheet_name='Massage Providers'):
        self.providers = load_providers(sheet_name)
        self.pending_requests = {}  # booking_id: timer
        self.active_bookings = {}   # Maps provider_phone -> booking_id
        logger.info(f"Initialized SMSBookingManager with {len(self.providers)} providers")

    def find_providers(self, location, massage_type, exclude=[]):
        # Simple filter logic, can be improved with geolocation
        filtered = [p for p in self.providers if p['Location'] == location and p['Type'] == massage_type and p['Phone'] not in exclude]
        return filtered

    def send_booking_request(self, booking_id, client_phone, location, massage_type, provider_phone):
        """Send a booking request to a provider and track the booking."""
        logger.info(f"Sending booking request {booking_id} to provider {provider_phone}")
        
        # Store the booking ID for this provider
        self.active_bookings[provider_phone] = booking_id
        
        # Format the message with the booking ID for tracking
        body = (
            f"📅 New Booking Request ({booking_id}):\n"
            f"Location: {location}\n"
            f"Service: {massage_type}\n"
            f"Client: {client_phone}\n"
            "\nReply YES to accept or NO to decline."
        )
        
        if not send_sms(provider_phone, body):
            logger.error(f"Failed to send booking request to {provider_phone}")
            return False
            
        # Start 15 min timer for provider response
        timer = Timer(900, self.handle_no_response, args=(booking_id, client_phone, location, massage_type, provider_phone))
        timer.start()
        self.pending_requests[booking_id] = timer
        return True

    def handle_provider_response(self, booking_id, provider_phone, response):
        """Handle a provider's response to a booking request."""
        logger.info(f"Processing response '{response}' for booking {booking_id} from {provider_phone}")
        
        # Look up booking ID if not provided
        if not booking_id and provider_phone in self.active_bookings:
            booking_id = self.active_bookings[provider_phone]
            logger.info(f"Found booking ID {booking_id} for provider {provider_phone}")
        
        if not booking_id:
            logger.error(f"No booking ID found for provider {provider_phone}")
            return False
            
        # Cancel the timeout timer
        timer = self.pending_requests.pop(booking_id, None)
        if timer:
            timer.cancel()
            logger.info(f"Cancelled timer for booking {booking_id}")
            
        # Clean up the active booking
        if provider_phone in self.active_bookings:
            del self.active_bookings[provider_phone]
            
        response = response.strip().upper()
        if response == "YES":
            logger.info(f"Provider {provider_phone} accepted booking {booking_id}")
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
