import os
import json
import time
import logging
import openai
from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_cors import CORS
from datetime import datetime, timedelta
import pytz
import requests
from dotenv import load_dotenv
from sms_booking import SMSBookingManager, send_sms
import re
import traceback
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log')
    ]
)

logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address
)
limiter.limit("200 per day; 50 per hour")

def clean_phone_number(number):
    """Basic phone number validation.
    
    Args:
        number: The phone number to validate
        
    Returns:
        str: The original number if valid, None if invalid
    """
    if not number:
        logger.warning("No phone number provided")
        return None
        
    try:
        # Convert to string and strip whitespace
        number_str = str(number).strip()
        
        # Basic validation - must contain at least 10 digits
        digits = sum(c.isdigit() for c in number_str)
        if digits < 10:
            logger.warning(f"Phone number too short: {number_str}")
            return None
            
        logger.info(f"Using phone number as-is: {number_str}")
        return number_str
        
    except Exception as e:
        logger.error(f"Error validating phone number '{number}': {str(e)}")
        return None

# Initialize OpenAI client
from openai import OpenAI

openai_api_key = os.getenv('OPENAI_API_KEY')
if not openai_api_key:
    raise ValueError("OPENAI_API_KEY environment variable not set")

# Initialize the OpenAI client
client = OpenAI(api_key=openai_api_key)

# For backward compatibility
OPENAI_API_KEY = openai_api_key

# Root endpoint to confirm the server is running
@app.route('/')
def index():
    return """
    <h1>Gold Touch Massage SMS Service</h1>
    <p>Server is running! </p>
    <h3>Test Endpoints:</h3>
    <ul>
        <li><a href="/sms-webhook" target="_blank">Test Webhook</a> - Check if the webhook is working</li>
        <li><code>POST /sms-webhook</code> - Handle incoming SMS (test with cURL)</li>
        <li><code>GET /sms-webhook?to=+1234567890</code> - Send a test SMS (replace with your number)</li>
    </ul>
    <h3>cURL Test Commands:</h3>
    <pre>
    # Test webhook with form data
    curl -X POST https://sms-yd7t.onrender.com/sms-webhook \
      -d "from=+1234567890&message=Hello"

    # Test webhook with JSON
    curl -X POST https://sms-yd7t.onrender.com/sms-webhook \
      -H "Content-Type: application/json" \
      -d '{"from": "+1234567890", "message": "Hello"}'
    </pre>
    """

# Initialize SMS booking manager (loads provider list from Google Sheets)
sms_manager = SMSBookingManager()

SYSTEM_PROMPT = (
    "You are Gold Touch Mobile Massage's friendly assistant, replying to SMS and Messenger messages.\n"
    "- Use a warm, conversational, and human tone.\n"
    "- Adapt your response to the user's sentiment (e.g., excited, curious, worried).\n"
    "- Here are some example questions and ideal answers. Feel free to rephrase or expand on these to match the user's tone or sentiment:\n\n"
    "Q: How much do your services cost?\n"
    "A: Our current massage rates:\n"
    "- 60 minutes · Mobile — $150\n"
    "- 90 minutes · Mobile — $200\n"
    "- 60 minutes · In-Studio — $120\n"
    "- 90 minutes · In-Studio — $170\n\n"
    "Q: What types of services do you offer?\n"
    "A: Swedish, deep tissue, lymphatic drainage and more!\n\n"
    "Q: Where are you located?\n"
    "A: Gold Touch Mobile is a mobile service, so we come to you. Some massage providers also offer in-studio appointments, but not all. You can check who offers studio sessions at goldtouchmobile.com/providers.\n\n"
    "If you notice the user is happy, excited, or has a specific sentiment, match their energy! Always offer to help with bookings or answer any other questions.\n"
)



# Booking endpoint
@app.route('/book', methods=['POST'])
def book():
    data = request.get_json()
    client_phone = data.get('client_phone')
    location = data.get('location')
    massage_type = data.get('massage_type')
    booking_id = data.get('booking_id') or os.urandom(8).hex()
    # Find suitable providers
    # For In-Studio, filter those with 'Yes' in 'In-Studio location...'
    # For Mobile, filter those with 'No' in 'In-Studio location...'
    providers = []
    for p in sms_manager.providers:
        in_studio = str(p.get('In-Studio location (yes/no, address)', '')).strip().lower().startswith('yes')
        if massage_type.lower() == 'in-studio' and in_studio and p.get('Based in', '').lower() == location.lower():
            providers.append(p)
        elif massage_type.lower() == 'mobile' and not in_studio and p.get('Based in', '').lower() == location.lower():
            providers.append(p)
    if providers:
        provider = providers[0]
        sms_manager.send_booking_request(
            booking_id,
            client_phone,
            location,
            massage_type,
            provider.get('Phone Number'))
        return jsonify({'status': 'Booking request sent', 'provider': provider.get('Name'), 'booking_id': booking_id}), 200
    else:
        return jsonify({'error': 'No providers found for this location/type'}), 404


# SMS webhook to handle incoming SMS (e.g., provider replies)
@app.route('/test-sms', methods=['GET', 'POST'])
def test_sms_endpoint():
    """Test endpoint to check if SMS webhook is working"""
    test_data = {
        'status': 'success',
        'message': 'Test endpoint is working',
        'timestamp': datetime.utcnow().isoformat(),
        'request': {
            'method': request.method,
            'headers': dict(request.headers),
            'form': dict(request.form),
            'json': request.get_json(silent=True),
            'args': dict(request.args)
        }
    }
    return jsonify(test_data), 200

@app.route('/sms-webhook', methods=['POST', 'GET'])
@limiter.limit("10 per minute")  # Rate limiting
@limiter.limit("100 per day")   # Additional rate limit
def sms_webhook():
    # Create a unique request ID for tracking
    import uuid
    import json
    import traceback
    
    request_id = str(uuid.uuid4())[:8]
    
    # Log the raw request data first
    try:
        logger.info(f"\n=== NEW REQUEST {request_id} ===")
        logger.info(f"Method: {request.method}")
        logger.info(f"URL: {request.url}")
        logger.info(f"Headers: {dict(request.headers)}")
        logger.info(f"Content-Type: {request.content_type}")
        logger.info(f"Form Data: {dict(request.form)}")
        logger.info(f"JSON Data: {request.get_json(silent=True)}")
        logger.info(f"Raw Data: {request.get_data().decode('utf-8', errors='replace')}")
    except Exception as e:
        logger.error(f"Error logging request data: {str(e)}\n{traceback.format_exc()}")
    
    # Log the raw request data first
    try:
        logger.info(f"\n=== RAW REQUEST DATA ===")
        logger.info(f"Method: {request.method}")
        logger.info(f"Headers: {dict(request.headers)}")
        logger.info(f"Content-Type: {request.content_type}")
        logger.info(f"Raw data: {request.get_data().decode('utf-8', errors='replace')}")
        logger.info(f"Form data: {dict(request.form)}")
        logger.info(f"JSON data: {request.get_json(silent=True)}")
    except Exception as e:
        logger.error(f"Error logging raw request: {str(e)}")
    
    debug_info = {
        'timestamp': datetime.utcnow().isoformat(),
        'request_id': request_id,
        'method': request.method,
        'headers': dict(request.headers),
        'form': dict(request.form),
        'json': request.get_json(silent=True),
        'args': dict(request.args),
        'remote_addr': request.remote_addr,
        'user_agent': str(request.user_agent),
        'content_type': request.content_type,
        'raw_data': request.get_data().decode('utf-8', errors='replace')
    }
    
    # Log the debug info
    logger.info(f"\n=== INCOMING MESSAGE DEBUG ===\n{json.dumps(debug_info, indent=2)}")
    
    try:
        logger.info(f"\n=== New Request ({request_id}) ===")
        logger.info(f"Time: {datetime.utcnow().isoformat()}")
        
        # Handle GET requests (for ClickSend verification)
        if request.method == 'GET':
            logger.info("Received GET request - ClickSend webhook verification")
            return "SMS Callback Request Successful", 200, {'Content-Type': 'text/plain'}
            
        # Log request details
        logger.info(f"[{request_id}] Method: {request.method}")
        logger.info(f"[{request_id}] URL: {request.url}")
        logger.info(f"[{request_id}] Headers: {dict(request.headers)}")
        logger.info(f"[{request_id}] Content-Type: {request.content_type}")
        logger.info(f"[{request_id}] Raw Data: {request.get_data()[:1000]}")  # Log first 1000 chars of raw data
        
        # Check content type and parse data
        if not request.is_json and not request.form:
            logger.error("No form or JSON data received")
            return jsonify({'error': 'No data received'}), 400
            
        data = request.form
        
        # Log all form fields for debugging
        logger.info(f"\n[{request_id}] === Form Data ===")
        for key, value in data.items():
            logger.info(f"[{request_id}] {key}: {value}")
        
        # Log JSON data if present
        if request.is_json:
            json_data = request.get_json()
            logger.info(f"[{request_id}] === JSON Data ===")
            logger.info(f"[{request_id}] {json_data}")
            
        # Log ClickSend specific fields
        clicksend_fields = ['to', 'from', 'body', 'message_id', 'timestamp', 'keyword', 'originalbody', 
                          'senderid', 'originalrecipient', 'date', 'source', 'type', 'network_code',
                          'network_name', 'country', 'price', 'status']
        for field in clicksend_fields:
            if field in data:
                logger.info(f"[{request_id}] ClickSend field - {field}: {data[field]}")
            
        # Log all available data for debugging
        logger.info(f"\n[{request_id}] === All Available Data ===")
        logger.info(f"[{request_id}] Request args: {request.args}")
        logger.info(f"[{request_id}] Request form: {request.form}")
        logger.info(f"[{request_id}] Request values: {request.values}")
        logger.info(f"[{request_id}] Request JSON: {request.get_json(silent=True)}")
        
        try:
            # Log all available data fields for debugging
            logger.info("\n=== All Available Data Fields ===")
            logger.info(f"Request method: {request.method}")
            logger.info(f"Content-Type: {request.content_type}")
            logger.info(f"Form data: {dict(request.form)}")
            logger.info(f"JSON data: {request.get_json(silent=True)}")
            logger.info(f"Raw data: {request.get_data().decode('utf-8', errors='replace')}")
            
            # Get data from form or JSON
            if request.is_json:
                json_data = request.get_json(silent=True) or {}
                data = {**data, **json_data}  # Merge with form data
                
            # Extract and clean message data - handle different field names from ClickSend
            from_number = clean_phone_number(
                data.get('from') or 
                data.get('sender') or 
                data.get('From') or 
                data.get('originalsenderid') or 
                data.get('sms') or
                data.get('from_number') or
                data.get('contact', {}).get('phone_number') or
                data.get('source') or
                data.get('source_number')
            )
            
            to_number = clean_phone_number(
                data.get('to') or 
                data.get('recipient') or 
                data.get('To') or 
                data.get('originalrecipient') or
                data.get('to_number') or
                data.get('destination', '').split(':')[-1] or  # Handle ClickSend's format
                data.get('target') or
                data.get('target_number')
            )
            
            body = (
                data.get('text') or 
                data.get('message') or 
                data.get('body') or 
                data.get('Body') or
                data.get('message_body') or
                data.get('content') or
                data.get('message_text') or
                data.get('sms_body') or
                ''
            )
            
            # Clean up the body
            if body is not None and not isinstance(body, str):
                body = str(body)
            body = (body or '').strip()
            
            # Log extracted data
            logger.info(f"\n=== Extracted Data ===")
            logger.info(f"From: {from_number} (type: {type(from_number)})")
            logger.info(f"To: {to_number} (type: {type(to_number)})")
            logger.info(f"Body: {body} (type: {type(body)})")
            
            # Validate required fields with more detailed error messages
            if not from_number:
                error_msg = f"Missing or invalid 'from' number in data: {dict(data)}"
                logger.error(error_msg)
                return jsonify({
                    'error': 'Invalid sender number format',
                    'details': error_msg,
                    'received_data': dict(data),
                    'request_headers': dict(request.headers),
                    'request_method': request.method,
                    'content_type': request.content_type
                }), 400
                
            if not to_number:
                error_msg = f"Missing or invalid 'to' number in data: {dict(data)}"
                logger.error(error_msg)
                return jsonify({
                    'error': 'Invalid recipient number format',
                    'details': error_msg,
                    'received_data': dict(data),
                    'request_headers': dict(request.headers),
                    'request_method': request.method,
                    'content_type': request.content_type
                }), 400
                
            if not body:
                logger.warning("Empty message body received")
                
        except Exception as e:
            error_msg = f"Error processing request: {str(e)}\n{traceback.format_exc()}"
            logger.error(error_msg)
            return jsonify({
                'error': 'Error processing request',
                'details': str(e),
                'request_headers': dict(request.headers),
                'request_method': request.method,
                'content_type': request.content_type,
                'raw_data': request.get_data().decode('utf-8', errors='replace')
            }), 400
            
        logger.info(f"📱 Processing SMS from {from_number} to {to_number}")
        logger.info(f"Message body: {body}")
        
        # Log all form fields for debugging
        for key, value in request.form.items():
            logger.info(f"Form field - {key}: {value}")
        
        # Check if this is a provider response to a booking (e.g., "YES" or "NO")
        if body and body.upper() in ['YES', 'NO']:
            # Try to find booking_id in custom_string or other fields
            booking_id = request.form.get('custom_string') or request.form.get('booking_id')
            logger.info(f"Processing provider response: {body} for booking_id: {booking_id}")
            
            if booking_id:
                sms_manager.handle_provider_response(booking_id, from_number, body)
                logger.info(f"Handled provider response for booking {booking_id}")
            else:
                logger.warning("Received YES/NO but no booking_id found")
                # Try to extract booking ID from message body if possible
                # Example: "YES book_1234567890"
                import re
                match = re.search(r'(book_\d+)', body)
                if match:
                    booking_id = match.group(1)
                    logger.info(f"Extracted booking_id from message: {booking_id}")
                    sms_manager.handle_provider_response(booking_id, from_number, body.split()[0].upper())
        else:
            # Handle other inbound messages (e.g., customer inquiries)
            logger.info(f"📩 New message from {from_number} to {to_number}: {body}")
            
            # Generate a dynamic response using OpenAI
            try:
                # Track conversation state (in a real app, you'd use a database)
                # For now, we'll keep it simple with just the last message
                
                # Define possible conversation paths
                greetings = ['hi', 'hello', 'hey', 'hi there', 'good morning', 'good afternoon', 'good evening']
                
                # Clean the message
                clean_body = body.lower().strip()
                
                # Check if it's a greeting
                if any(greeting in clean_body for greeting in greetings):
                    response_text = "Hi there! 😊 How can I help?"
                    
                # Check for thanks/bye
                elif any(word in clean_body for word in ['thank', 'thanks', 'bye', 'goodbye']):
                    response_text = "You're welcome! Have a great day! 🌟"
                    
                # Check for yes/affirmative responses
                elif any(word in clean_body for word in ['yes', 'yeah', 'sure', 'ok', 'yep']):
                    response_text = "Great! You can book your appointment at goldtouchmobile.com/providers 😊"
                    
                # Check for pricing questions
                elif any(word in clean_body for word in ['price', 'cost', 'how much', 'rate', 'rates']):
                    response_text = """Here's our pricing:

🚗 Mobile (we come to you):
60 min — $150
90 min — $200

🏡 In-Studio:
60 min — $120
90 min — $170

Book at goldtouchmobile.com/providers"""
                
                # Check for booking/questions
                elif any(word in clean_body for word in ['book', 'schedule', 'appointment', 'available']):
                    response_text = "Book at goldtouchmobile.com/providers 😊"
                    
                # Check for location questions
                elif any(word in clean_body for word in ['where', 'location', 'address', 'come to', 'studio', 'based']):
                    response_text = "We come to you! Some providers offer in-studio too. Check goldtouchmobile.com/providers"
                    
                # Check for services
                elif any(word in clean_body for word in ['massage', 'service', 'swedish', 'deep tissue', 'prenatal']):
                    response_text = "We do Swedish, deep tissue, and prenatal. What type are you interested in?"
                    
                # Check for availability questions
                elif any(word in clean_body for word in ['available', 'availability', 'schedule', 'openings', 'appointment']):
                    response_text = "Yes we do! The quickest and easiest way to book is at goldtouchmobile.com/providers 😊"
                    
                # Only use AI if no specific response was triggered
                if 'response_text' not in locals() or response_text is None:
                    try:
                        # Define prompt inside the try block
                        prompt = f"""You're having a friendly SMS conversation for Gold Touch Massage. 
                        The client just said: "{body}"
                        
                        Keep your response:
                        - Short and sweet (1-2 sentences max)
                        - Casual and friendly
                        - No prices or durations
                        - End with a question to keep the conversation going
                        """
                        
                        # Generate response using OpenAI
                        response = client.chat.completions.create(
                            model="gpt-4",
                            messages=[
                                {"role": "system", "content": "You are a friendly massage therapist assistant. Keep responses short, warm, and conversational."},
                                {"role": "user", "content": prompt}
                            ],
                            max_tokens=150,
                            temperature=0.7,
                        )
                        response_text = response.choices[0].message.content.strip()
                        logger.info(f"Generated response: {response_text}")
                    except Exception as e:
                        logger.error(f"AI response error: {str(e)}", exc_info=True)
                        # More engaging default message with booking link
                        response_text = """Hi there! 😊 Thanks for your message! 

You can book a massage 24/7 at: goldtouchmobile.com/providers

Or just reply with your preferred day/time and we'll help you out! 💆‍♀️✨"""
                        logger.info("Using fallback response")
                        
                logger.info(f"Generated response: {response_text}")
                
            except Exception as e:
                logger.error(f"Error generating AI response: {str(e)}", exc_info=True)
                # More engaging default message with booking link
                response_text = """Hi there! 😊 Thanks for your message! 

You can book a massage 24/7 at: goldtouchmobile.com/providers

Or just reply with your preferred day/time and we'll help you out! 💆‍♀️✨"""
                logger.info("Using fallback response")
                
                # Log the full error for debugging
                import traceback
                logger.error(f"Full error: {traceback.format_exc()}")
            
            # Send the response back to the sender
            try:
                logger.info(f"Sending response to {from_number} from {to_number}")
                success, message = send_sms(
                    to=from_number, 
                    body=response_text, 
                    from_number=to_number
                )
                
                if success:
                    logger.info(f"Successfully sent response to {from_number}")
                    response_data = {
                        'status': 'success',
                        'message': 'Message processed and response sent',
                        'to': from_number,
                        'from': to_number,
                        'timestamp': datetime.utcnow().isoformat()
                    }
                    return jsonify(response_data), 200
                else:
                    logger.error(f"Failed to send SMS: {message}")
                    return jsonify({
                        'status': 'error',
                        'message': 'Failed to send response',
                        'error': str(message)
                    }), 500
                    
            except Exception as send_error:
                logger.error(f"Error sending SMS: {str(send_error)}", exc_info=True)
                return jsonify({
                    'status': 'error',
                    'message': 'Error sending response',
                    'error': str(send_error)
                }), 500
                
    except Exception as e:
        logger.error(f"❌ Unhandled error in sms_webhook: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': 'Internal server error',
            'error': str(e)
        }), 500

# Webhook endpoint for Fluent Forms Pro integration
@app.route('/fluentforms-webhook', methods=['POST'])
def fluentforms_webhook():
    try:
        # Get form data
        data = request.get_json(silent=True) or request.form
        
        # Extract form fields (adjust these to match your form field names)
        name = data.get('name', 'Customer')
        phone = data.get('phone', '')
        service_type = data.get('service_type', 'Mobile')  # e.g., 'Mobile' or 'In-Studio'
        location = data.get('location', 'Unknown Location')
        notes = data.get('notes', 'No additional notes')
        
        # Generate a unique booking ID
        booking_id = f"book_{int(time.time())}"
        
        # Find available providers (modify this based on your provider selection logic)
        providers = sms_manager.find_providers(location, service_type)
        
        if not providers:
            return jsonify({
                'status': 'error',
                'message': 'No providers available for the selected service/location.'
            }), 404
        
        # Select the first available provider (or implement your own logic)
        provider = providers[0]
        provider_phone = provider.get('Phone Number')
        
        # Send booking request to the provider
        sms_manager.send_booking_request(
            booking_id=booking_id,
            client_phone=phone,
            location=location,
            massage_type=service_type,
            provider_phone=provider_phone
        )
        
        # Optional: Send confirmation to the client
        client_message = (
            f"Hi {name}, we've received your booking request for {service_type} massage at {location}. "
            f"We've notified a provider and will confirm your appointment shortly!"
        )
        # Uncomment to enable SMS confirmation to client
        # send_sms(phone, client_message)
        
        return jsonify({
            'status': 'success',
            'message': 'Booking request sent to provider',
            'booking_id': booking_id,
            'provider': provider.get('Name')
        }), 200
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/test-sms', methods=['GET'])
def test_sms():
    """Test endpoint to send an SMS to a specified number."""
    test_number = request.args.get('to')
    if not test_number:
        return jsonify({'error': 'Missing "to" parameter (e.g., /test-sms?to=+1234567890)'}), 400
    
    message = "🔧 This is a test message from the Gold Touch Massage system!"
    logger.info(f"Sending test SMS to {test_number}: {message}")
    
    try:
        success = send_sms(test_number, message)
        if success:
            logger.info(f"SMS sent successfully to {test_number}")
            return jsonify({
                'status': 'success',
                'message': f'Sent test SMS to {test_number}'
            })
        else:
            logger.error(f"Failed to send SMS to {test_number}")
            return jsonify({
                'status': 'error',
                'message': 'Failed to send SMS. Check server logs for details.'
            }), 500
    except Exception as e:
        logger.error(f"Error sending SMS: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Error: {str(e)}'
        }), 500

# Test endpoint to verify webhook is working
@app.route('/test-webhook', methods=['GET', 'POST'])
def test_webhook():
    """Test endpoint to verify the webhook is working.
    
    This endpoint can be tested in multiple ways:
    1. Visit in a browser (GET request)
    2. Send a POST request with form data
    3. Send a POST request with JSON data
    """
    # Log the incoming request
    logger.info("\n=== Test Webhook Called ===")
    logger.info(f"Method: {request.method}")
    logger.info(f"Headers: {dict(request.headers)}")
    
    # Parse form data if it exists
    form_data = {}
    if request.form:
        form_data = dict(request.form)
        logger.info(f"Form data: {form_data}")
    
    # Parse JSON data if it exists
    json_data = {}
    if request.is_json:
        json_data = request.get_json() or {}
        logger.info(f"JSON data: {json_data}")
    
    # Prepare response
    response_data = {
        'status': 'success',
        'service': 'Gold Touch Massage SMS Webhook',
        'method': request.method,
        'timestamp': int(time.time()),
        'form_data': form_data,
        'json_data': json_data,
        'headers': {k: v for k, v in request.headers.items()}
    }
    logger.info(f"Returning response: {response_data}")
    
    # Return the response
    return jsonify(response_data), 200

# Test endpoint to verify webhook connectivity (GET request for browser testing)
# Keep-alive endpoint for uptime monitoring
@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({
        'status': 'alive', 
        'time': time.time(),
        'service': 'Gold Touch Massage SMS Service'
    }), 200

# Simple uptime monitor that pings itself every 5 minutes
import threading
def keep_alive():
    import urllib.request
    import time
    while True:
        try:
            urllib.request.urlopen('https://sms-yd7t.onrender.com/ping')
        except Exception as e:
            logger.warning(f"Keep-alive ping failed: {e}")
        time.sleep(300)  # 5 minutes

# Start the keep-alive thread when the app starts
if not os.environ.get('WERKZEUG_RUN_MAIN'):
    threading.Thread(target=keep_alive, daemon=True).start()

@app.route('/test-ai', methods=['GET'])
def test_ai():
    """Test endpoint to verify OpenAI connectivity"""
    try:
        test_prompt = "Just say 'AI is working! 😊'"
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": test_prompt}
            ],
            max_tokens=20
        )
        result = response.choices[0].message.content.strip()
        return jsonify({
            'status': 'success',
            'response': result,
            'model': response.model
        })
    except Exception as e:
        logger.error(f"OpenAI test failed: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e),
            'hint': 'Make sure OPENAI_API_KEY is set correctly in environment variables.'
        }), 500

if __name__ == '__main__':
    # Use the PORT environment variable if available, otherwise default to 5000
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
