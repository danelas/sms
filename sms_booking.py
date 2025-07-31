import os
import time
import logging
import traceback
from threading import Timer
from load_providers import load_providers
import requests

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# TextMagic SMS config
TEXTMAGIC_USERNAME = os.getenv('TEXTMAGIC_USERNAME')
TEXTMAGIC_API_KEY = os.getenv('TEXTMAGIC_API_KEY')
TEXTMAGIC_FROM = os.getenv('TEXTMAGIC_FROM', 'GoldTouch')  # Must be a verified sender ID or number

# Helper to send SMS using ClickSend

def send_sms(to, body, from_number=None):
    """Send an SMS using TextMagic.
    
    Args:
        to (str): Recipient phone number in international format (e.g., '+1234567890')
        body (str): Message content
        from_number (str, optional): Sender ID or number. Defaults to TEXTMAGIC_FROM.
        
    Returns:
        tuple: (success: bool, message: str)
    """
    logger = logging.getLogger(__name__)
    logger.info("=== Starting send_sms function ===")
    
    try:
        # Log environment variables (without sensitive values)
        logger.info(f"TEXTMAGIC_USERNAME: {'Set' if TEXTMAGIC_USERNAME else 'Not set'}")
        logger.info(f"TEXTMAGIC_FROM: {TEXTMAGIC_FROM}")
        
        # Use provided from_number or fall back to TEXTMAGIC_FROM
        from_number = str(from_number or TEXTMAGIC_FROM).strip()
        to = str(to).strip() if to else None
        
        logger.info(f"Validating phone numbers - To: {to}, From: {from_number}")
        
        # Basic validation - must contain at least 10 digits
        if not to or sum(c.isdigit() for c in to) < 10:
            error_msg = f"Invalid 'to' number format. Must contain at least 10 digits. Got: {to}"
            logger.error(error_msg)
            return False, error_msg
        
        logger.info(f"Preparing to send SMS - From: '{from_number}', To: '{to}', Body: '{body[:50]}...'")
        
        # Initialize TextMagic client
        from textmagic import TextmagicRestClient
        client = TextmagicRestClient(TEXTMAGIC_USERNAME, TEXTMAGIC_API_KEY)
        
        # Send the message
        result = client.messages.create(
            phones=to.replace('+', ''),  # TextMagic doesn't want the + in the number
            text=body,
            from_company=from_number if from_number.isalpha() else None,
            from_number=from_number if from_number.startswith('+') else None
        )
        
        # Check if the message was sent successfully
        if hasattr(result, 'id'):
            logger.info(f"SMS sent successfully to {to}, message ID: {result.id}")
            return True, "Message sent successfully"
        else:
            error_msg = f"Failed to send SMS. Response: {result}"
            logger.error(error_msg)
            return False, error_msg
            
    except Exception as e:
        error_msg = f"Failed to send SMS: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        return False, error_msg

# Booking logic class
class SMSBookingManager:
    def __init__(self, sheet_name='Massage Providers'):
        self.providers = load_providers(sheet_name)
        self.pending_requests = {}  # booking_id: timer
        self.active_bookings = {}   # Maps provider_phone -> booking_info
        self.booking_attempts = {}  # booking_id: {tried_providers: [], client_info: {...}}
        logger.info(f"Initialized SMSBookingManager with {len(self.providers)} providers")

    def find_providers(self, location, massage_type, exclude=[]):
        """
        Find available providers for a given location and service type.
        Handles providers who offer multiple service types (e.g., 'Mobile, In-Studio')
        """
        filtered = []
        for p in self.providers:
            # Skip if provider is in exclude list
            if p['Phone'] in exclude:
                continue
                
            # Check location (case-insensitive)
            if p['Location'].lower() != location.lower():
                continue
                
            # Get provider's service types (split by comma and strip whitespace)
            provider_types = [t.strip().lower() for t in p['Type'].split(',')]
            
            # Check if provider offers the requested service type
            if massage_type.lower() in provider_types:
                filtered.append(p)
                
        logger.info(f"Found {len(filtered)} providers for location '{location}' and service type '{massage_type}'")
        return filtered
        
    def find_provider_by_name(self, name, location, service_type):
        """Find a specific provider by name, location, and service type"""
        name = name.lower().strip()
        for p in self.providers:
            if (p['Name'].lower() == name and 
                p['Location'].lower() == location.lower() and
                service_type.lower() in [t.strip().lower() for t in p['Type'].split(',')]):
                return [p]
        return []

    def send_booking_request(self, booking_id, client_phone, location, massage_type, provider_phone, client_name='Client'):
        """Send a booking request to a provider and track the booking."""
        logger.info(f"Sending booking request {booking_id} to provider {provider_phone}")
        
        # Store the booking info for this provider
        self.active_bookings[provider_phone] = {
            'booking_id': booking_id,
            'client_phone': client_phone,
            'client_name': client_name,
            'location': location,
            'massage_type': massage_type,
            'status': 'pending'
        }
        
        # Get provider name for the message
        provider_name = next((p['Name'] for p in self.providers 
                           if p['Phone'] == provider_phone), 'Therapist')
        
        # Format the message with the booking ID for tracking
        body = (
            f"{provider_name}, you have a new booking request!\n\n"
            f"Client: {client_name}\n"
            f"Phone: {client_phone}\n"
            f"Service: {massage_type}\n"
            f"Location: {location}\n"
            f"\n"
            f"Reply with:\n"
            f"YES to accept\n"
            f"NO to decline\n"
            f"\n"
            f"(Booking ID: {booking_id})\n"
            f"\nYou have 15 minutes to respond."
        )
        
        # Send the SMS
        success, error = send_sms(provider_phone, body)
        
        if success:
            # Start 15 min timer for provider response
            timer = Timer(900, self.handle_no_response, args=(booking_id, provider_phone))
            timer.start()
            self.pending_requests[booking_id] = {
                'timer': timer,
                'provider_phone': provider_phone,
                'attempts': 1
            }
            
            # Track this provider attempt
            if booking_id not in self.booking_attempts:
                self.booking_attempts[booking_id] = {
                    'tried_providers': [],
                    'client_info': {
                        'phone': client_phone,
                        'name': client_name,
                        'service_type': massage_type,
                        'location': location
                    },
                    'status': 'pending'
                }
            self.booking_attempts[booking_id]['tried_providers'].append(provider_phone)
            
            return True, None
        else:
            logger.error(f"Failed to send booking request to {provider_phone}: {error}")
            return False, error           

    def handle_provider_response(self, provider_phone, response_text):
        """Handle a provider's response to a booking request."""
        logger.info(f"Processing response from {provider_phone}: {response_text}")
        
        # Get the booking info for this provider
        if provider_phone not in self.active_bookings:
            logger.error(f"No active booking found for provider {provider_phone}")
            send_sms(provider_phone, "We couldn't find an active booking. Please contact support.")
            return False
            
        booking_info = self.active_bookings[provider_phone]
        booking_id = booking_info['booking_id']
        client_phone = booking_info['client_phone']
        client_name = booking_info['client_name']
        location = booking_info['location']
        massage_type = booking_info['massage_type']
        
        # Cancel the timeout timer
        if booking_id in self.pending_requests:
            self.pending_requests[booking_id]['timer'].cancel()
            del self.pending_requests[booking_id]
            
        # Get provider name for messages
        provider_name = next((p['Name'] for p in self.providers 
                           if p['Phone'] == provider_phone), 'a therapist')
        
        # Process the response
        response = response_text.strip().upper()
        if response == 'YES':
            # Provider accepted the booking
            logger.info(f"Provider {provider_name} accepted booking {booking_id}")
            
            # Confirm to provider
            send_sms(
                provider_phone,
                f"Thank you for accepting the booking!\n\n"
                f"Client: {client_name}\n"
                f"Phone: {client_phone}\n"
                f"Service: {massage_type}\n"
                f"Location: {location}\n\n"
                f"Please contact the client to confirm the exact time and address."
            )
            
            # Notify client
            client_msg = (
                f"Great news! {provider_name} has accepted your booking for "
                f"{massage_type} in {location}. They'll contact you shortly to confirm details."
            )
            send_sms(client_phone, client_msg)
            
            # Update booking status
            if booking_id in self.booking_attempts:
                self.booking_attempts[booking_id]['status'] = 'confirmed'
                self.booking_attempts[booking_id]['confirmed_provider'] = provider_phone
            
            # Clean up
            self.cleanup_booking(booking_id, provider_phone)
            return True
            
        else:
            # Provider declined or sent an invalid response
            logger.info(f"Provider {provider_name} declined booking {booking_id}")
            
            # Send acknowledgment to provider
            send_sms(
                provider_phone,
                "Thank you for your response. We'll reach out for future bookings!"
            )
            
            # Try to find another provider
            self.find_next_provider(booking_id, provider_phone)
            return False
                
        # If we have client info, try to find another provider
        if client_phone and location and massage_type:
            logger.info(f"Looking for alternative providers for declined booking {booking_id}")
            excluded = list(self.active_bookings.keys()) + [provider_phone]
            providers = self.find_providers(location, massage_type, exclude=excluded)
            
            if providers:
                next_provider = providers[0]
                logger.info(f"Found alternative provider: {next_provider.get('Name')} - {next_provider.get('Phone')}")
                
                # Store client info with the booking
                self.active_bookings[next_provider['Phone']] = {
                    'booking_id': booking_id,
                    'client_phone': client_phone,
                    'client_name': client_name,
                    'location': location,
                    'massage_type': massage_type,
                    'status': 'pending'
                }
                
                # Send new booking request
                success, error = self.send_booking_request(
                    booking_id=booking_id,
                    client_phone=client_phone,
                    client_name=client_name,
                    location=location,
                    massage_type=massage_type,
                    provider_phone=next_provider.get('Phone')
                )
                
                if not success:
                    logger.error(f"Failed to contact next provider: {error}")
                    # Try the next provider
                    return self.find_next_provider(booking_id, next_provider['Phone'])
            else:
                # No more providers available
                logger.info(f"No alternative providers available for booking {booking_id}")
                send_sms(
                    client_phone,
                    f"We're sorry, but we couldn't find an available provider for "
                    f"{massage_type} in {location}. Please try again later or contact us for assistance."
                )

    def handle_no_response(self, booking_id, provider_phone):
        """Handle case when provider doesn't respond in time."""
        logger.info(f"No response from provider {provider_phone} for booking {booking_id}")
        
        # Get provider name for logging
        provider_name = next((p['Name'] for p in self.providers 
                           if p['Phone'] == provider_phone), 'a therapist')
        
        # Notify the provider
        send_sms(
            provider_phone,
            f"This booking request has been reassigned since we didn't hear back from you. "
            f"We'll contact you for future opportunities."
        )
        
        # Clean up the booking
        self.cleanup_booking(booking_id, provider_phone)
        
        # Try the next available provider
        self.find_next_provider(booking_id, provider_phone)

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
