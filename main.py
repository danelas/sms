import os
import json
import time
import logging
import openai
from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_cors import CORS
from datetime import datetime, timedelta, timezone
import pytz
import requests
from dotenv import load_dotenv
from sms_booking import SMSBookingManager, send_sms
from crm_integration import save_contact_to_crm, log_communication
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

# Get OpenAI API key from environment
openai_api_key = os.getenv('OPENAI_API_KEY')
if not openai_api_key:
    raise ValueError("OPENAI_API_KEY environment variable not set")

# Initialize the OpenAI client with the latest configuration
client = OpenAI(
    api_key=openai_api_key,
    # Add organization if needed
    # organization='org-xxx',  # Optional: Add your organization ID if using one
)

# For backward compatibility
OPENAI_API_KEY = openai_api_key

# Verify API key works
try:
    client.models.list()
    logger.info("Successfully connected to OpenAI API")
except Exception as e:
    logger.error(f"Failed to connect to OpenAI API: {str(e)}")
    logger.error("Please check your API key and ensure it has the correct permissions")

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

# Dictionary to track recent message IDs to prevent duplicate processing
import threading
import sqlite3
import os
from datetime import datetime, timedelta, timezone

# Initialize SQLite database for persistent storage
def init_db():
    db_path = 'vip_messages.db'
    new_db = not os.path.exists(db_path)
    
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cursor = conn.cursor()
    
    if new_db:
        cursor.execute('''
            CREATE TABLE vip_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_number TEXT NOT NULL,
                to_number TEXT NOT NULL,
                scheduled_time TIMESTAMP NOT NULL,
                sent BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
    
    return conn

# Initialize database connection
DB_CONN = init_db()

def schedule_vip_message(from_number, to_number, delay_minutes=3):
    """Schedule a VIP message to be sent after the specified delay"""
    try:
        conv_key = f"{from_number}:{to_number}"
        logger.info(f"[VIP] Starting to schedule message for {conv_key} in {delay_minutes} minutes")
        
        # Cancel any existing timer for this conversation
        if conv_key in VIP_TIMERS:
            logger.info(f"[VIP] Canceling existing timer for {conv_key}")
            try:
                VIP_TIMERS[conv_key].cancel()
            except Exception as e:
                logger.error(f"[VIP] Error canceling existing timer: {e}")
        
        # Create a new timer
        def send_vip():
            try:
                logger.info(f"[VIP] Timer triggered for {conv_key}, sending VIP message to {from_number}")
                success = send_vip_message(from_number, to_number)
                if success:
                    logger.info(f"[VIP] Successfully sent VIP message to {from_number}")
                else:
                    logger.error(f"[VIP] Failed to send VIP message to {from_number}")
                
                # Clean up the timer
                if conv_key in VIP_TIMERS:
                    del VIP_TIMERS[conv_key]
                    logger.info(f"[VIP] Removed timer for {conv_key}")
            except Exception as e:
                logger.error(f"[VIP] Error in VIP timer for {conv_key}: {e}", exc_info=True)
        
        # Schedule the new timer
        timer = threading.Timer(delay_minutes * 60, send_vip)
        timer.daemon = True  # Allow program to exit even if timer is running
        timer.start()
        
        # Store the timer
        VIP_TIMERS[conv_key] = timer
        
        logger.info(f"[VIP] Successfully scheduled VIP message to {from_number} in {delay_minutes} minutes")
        logger.info(f"[VIP] Current timers: {list(VIP_TIMERS.keys())}")
        return True
    except Exception as e:
        logger.error(f"[VIP] Error in schedule_vip_message: {e}", exc_info=True)
        return False

def get_pending_vip_messages():
    """Stub for compatibility - not used with timer-based approach"""
    return []

def mark_vip_message_sent(message_id):
    """Stub for compatibility - not used with timer-based approach"""
    return True

# Dictionary to track recent message IDs to prevent duplicate processing
RECENT_MESSAGES = {}
MESSAGE_LOCK = threading.Lock()

# Dictionary to track conversation state and VIP timers
CONVERSATION_STATE = {}
VIP_TIMERS = {}

# Generate a unique key for message deduplication
def get_message_key(from_number, to_number, body):
    """Generate a unique key for message deduplication"""
    return f"{from_number}:{to_number}:{body.lower().strip()}"

@app.route('/sms-webhook', methods=['POST', 'GET'])
@limiter.limit("10 per minute")  # Rate limiting
@limiter.limit("100 per day")   # Additional rate limit
def sms_webhook():
    # Create a unique request ID for tracking
    import uuid
    import json
    import traceback
    import time
    
    # Generate a unique request ID
    request_id = str(uuid.uuid4())[:8]
    logger = logging.getLogger(__name__)
    
    # Log the start of request processing
    logger.info(f"\n=== NEW REQUEST {request_id} ===")
    
    # Clean up old message IDs (older than 5 minutes)
    current_time = time.time()
    with MESSAGE_LOCK:
        # Clean up old entries from RECENT_MESSAGES
        for msg_key in list(RECENT_MESSAGES.keys()):
            if current_time - RECENT_MESSAGES[msg_key]['timestamp'] > 300:  # 5 minutes
                del RECENT_MESSAGES[msg_key]
    
    # Log request details
    try:
        logger.info(f"\n=== REQUEST DETAILS ===")
        logger.info(f"Time: {datetime.utcnow().isoformat()}")
        logger.info(f"Method: {request.method}")
        logger.info(f"URL: {request.url}")
        logger.info(f"Headers: {dict(request.headers)}")
        logger.info(f"Content-Type: {request.content_type}")
        
        # Get raw data for logging
        raw_data = request.get_data().decode('utf-8', errors='replace')
        logger.info(f"Raw Data (first 1000 chars): {raw_data[:1000]}")
        
        # Try to parse JSON if content-type is application/json
        json_data = None
        if request.is_json:
            try:
                json_data = request.get_json()
                logger.info(f"JSON Data: {json.dumps(json_data, indent=2)}")
            except Exception as e:
                logger.error(f"Failed to parse JSON: {str(e)}")
        
        # Log form data if present
        if request.form:
            logger.info("Form Data:")
            for key, value in request.form.items():
                logger.info(f"  {key}: {value}")
                
    except Exception as e:
        logger.error(f"Error in request logging: {str(e)}\n{traceback.format_exc()}")
    
    # Handle GET requests (for webhook verification)
    if request.method == 'GET':
        logger.info("Received GET request - webhook verification")
        return "SMS Webhook is working", 200, {'Content-Type': 'text/plain'}
    
    # Parse incoming message data
    try:
        # Initialize variables
        from_number = None
        to_number = None
        message_body = None
        
        # Try to get data from JSON
        if request.is_json and json_data:
            from_number = json_data.get('from') or json_data.get('From') or json_data.get('sender')
            to_number = json_data.get('to') or json_data.get('To') or json_data.get('recipient')
            message_body = json_data.get('body') or json_data.get('Body') or json_data.get('message')
        
        # Try to get data from form
        if not all([from_number, to_number, message_body]):
            from_number = request.form.get('from') or request.form.get('From') or request.form.get('sender')
            to_number = request.form.get('to') or request.form.get('To') or request.form.get('recipient')
            message_body = request.form.get('body') or request.form.get('Body') or request.form.get('message')
        
        # Try to get from args if still not found
        if not all([from_number, to_number, message_body]):
            from_number = request.args.get('from') or request.args.get('From')
            to_number = request.args.get('to') or request.args.get('To')
            message_body = request.args.get('body') or request.args.get('Body')
        
        # Log parsed data
        logger.info(f"\n=== PARSED MESSAGE ===")
        logger.info(f"From: {from_number}")
        logger.info(f"To: {to_number}")
        logger.info(f"Message: {message_body}")
        
        # Validate required fields
        if not all([from_number, to_number, message_body]):
            error_msg = f"Missing required fields. From: {from_number}, To: {to_number}, Message: {message_body}"
            logger.error(error_msg)
            return jsonify({'status': 'error', 'message': error_msg}), 400
            
        # Clean the phone numbers
        from_number = clean_phone_number(from_number)
        to_number = clean_phone_number(to_number)
        
        if not from_number or not to_number:
            error_msg = f"Invalid phone numbers. From: {from_number}, To: {to_number}"
            logger.error(error_msg)
            return jsonify({'status': 'error', 'message': error_msg}), 400
            
        # Save contact to CRM (in a background thread to not block the response)
        def save_contact():
            try:
                # Save the contact to CRM
                success, message = save_contact_to_crm(
                    phone_number=from_number,
                    name=json_data.get('name') if json_data else None,
                    email=json_data.get('email') if json_data else None,
                    custom_fields={
                        'last_message': message_body[:500],
                        'source': 'SMS Webhook',
                        'first_seen': datetime.utcnow().isoformat(),
                        'last_contacted': datetime.utcnow().isoformat()
                    }
                )
                logger.info(f"CRM contact save result: {success} - {message}")
                
                # Log the communication
                log_success = log_communication(
                    phone_number=from_number,
                    direction='inbound',
                    message=message_body,
                    status='received'
                )
                if not log_success:
                    logger.warning("Failed to log communication to CRM")
                    
            except Exception as e:
                logger.error(f"Error in CRM operations: {str(e)}\n{traceback.format_exc()}")
        
        # Start the CRM operations in a background thread
        import threading
        threading.Thread(target=save_contact, daemon=True).start()
        
        # Check for duplicate message
        message_key = get_message_key(from_number, to_number, message_body)
        with MESSAGE_LOCK:
            if message_key in RECENT_MESSAGES:
                logger.warning(f"Duplicate message detected: {message_key}")
                return jsonify({'status': 'success', 'message': 'Duplicate message ignored'}), 200
                
            # Add to recent messages
            RECENT_MESSAGES[message_key] = {
                'timestamp': current_time,
                'from': from_number,
                'to': to_number,
                'body': message_body
            }
        
        # Process the message (this is where you'd add your business logic)
        logger.info(f"Processing message from {from_number} to {to_number}")
        
        # Example: Echo the message back
        response_message = f"Received your message: {message_body}"
        send_success, send_result = send_sms(
            to=from_number,
            body=response_message,
            from_number=to_number
        )
        
        if not send_success:
            logger.error(f"Failed to send response: {send_result}")
            return jsonify({'status': 'error', 'message': 'Failed to send response'}), 500
            
        logger.info(f"Successfully processed message from {from_number}")
        return jsonify({'status': 'success', 'message': 'Message processed'}), 200
        
    except Exception as e:
        error_msg = f"Error processing message: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        
        # Log all available data for debugging
        logger.info("\n=== ERROR DEBUGGING INFO ===")
        logger.info(f"Request method: {request.method}")
        logger.info(f"Content-Type: {request.content_type}")
        logger.info(f"Form data: {dict(request.form)}")
        logger.info(f"JSON data: {request.get_json(silent=True)}")
        
        return jsonify({'status': 'error', 'message': error_msg}), 500
            
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
                # For now, we'll use a simple in-memory dictionary
                if not hasattr(sms_webhook, 'conversation_history'):
                    sms_webhook.conversation_history = {}
                
                # Initialize conversation history for this number if it doesn't exist
                if from_number not in sms_webhook.conversation_history:
                    sms_webhook.conversation_history[from_number] = []
                
                # Get the conversation history for this number
                conversation_history = sms_webhook.conversation_history[from_number]
                
                # Keep only the last 4 messages to avoid context window issues
                # (2 exchanges: 1 user message + 1 assistant response)
                conversation_history = conversation_history[-4:]
                sms_webhook.conversation_history[from_number] = conversation_history
                
                # Clean the message
                clean_body = body.lower().strip()
                
                # Only use hardcoded responses for very simple messages
                # For anything more complex, let the AI handle it
                response_text = None
                
                # Check if it's JUST a greeting (no other words)
                if any(clean_body == greeting.lower() for greeting in ['hi', 'hello', 'hey', 'hi there', 'good morning', 'good afternoon', 'good evening']):
                    response_text = "Hi there! 😊 How can I help?"
                
                # Check for simple thanks/bye (exact matches only)
                elif clean_body in ['thanks', 'thank you', 'bye', 'goodbye', 'thank you!']:
                    response_text = "You're welcome! Have a great day! 🌟"
                
                # Check for pricing questions
                elif any(word in clean_body for word in ['price', 'cost', 'how much', 'rate', 'rates']):
                    response_text = "Our massage services start at $120/hour for in-studio sessions with select providers who have their own studio, and $150/hour for mobile services. You can see all our pricing and book at goldtouchmobile.com/providers"
                    
                # Check for service questions
                elif any(word in clean_body for word in ['service', 'massage type', 'offer', 'swedish', 'deep tissue', 'sports', 'prenatal']):
                    response_text = "We offer Swedish, Deep Tissue, Sports, and Prenatal massages. What type are you interested in? 😊"
                    
                # Check for location questions
                elif any(word in clean_body for word in ['where', 'location', 'address', 'come to me', 'mobile', 'outcall', 'in-home']):
                    response_text = "We offer mobile massage services where we come to you! Some providers also have in-studio options. You can see who's available at goldtouchmobile.com/providers 😊"
                    
                # Remove the hardcoded availability response and let the AI handle it
                # This allows for more natural responses to nuanced questions
                    
                # For all other messages, set response_text to None to trigger AI response
                # Use AI for most responses to maintain natural conversation flow
                if 'response_text' not in locals() or response_text is None:
                    try:
                        # System prompt with instructions and knowledge
                        system_prompt = """You are a friendly and knowledgeable massage therapist assistant for Gold Touch Massage. 
                        Respond to customer inquiries in a warm, conversational tone while being helpful and informative.
                        
                        Key Information to Use Naturally:
                        - Booking: The easiest way to book is through our website at goldtouchmobile.com/providers where you can see real-time availability
                        - Services: We offer Swedish, Deep tissue, Reflexology, Sports Massage, and more
                        - Pricing (only mention when asked):
                          🚗 Mobile/Outcall massage: 60 min - $150, 90 min - $200 (we come to you!)
                          🏡 Some independent providers offer in-studio options starting at $120 (availability shown on booking page)
                        - Important: We're primarily a mobile/outcall service - in-studio options are only available with select independent providers
                        
                        Terminology Notes:
                        - 'Mobile' and 'Outcall' mean the same thing - a therapist comes to your location
                        - When someone asks about 'outcall', respond as if they asked about 'mobile' service
                        - Use 'mobile' in your responses for consistency
                        
                        Response Guidelines:
                        1. Keep responses short and to the point (1-2 sentences max)
                        2. Avoid asking follow-up questions unless absolutely necessary
                        3. Always include the booking link: goldtouchmobile.com/providers
                        4. Be friendly but concise
                        5. Don't ask for information we don't need
                        6. If they mention a specific service, acknowledge it briefly
                        7. No need to list all service options unless specifically asked
                        
                        Example Flows:
                        
                        User: I'd like to book a massage are you available now?
                        You: "Hello! You can check our real-time availability and book at goldtouchmobile.com/providers"
                        
                        User: Swedish massage
                        You: "Great choice! You can see available times and book your Swedish massage at goldtouchmobile.com/providers"
                        
                        User: What's your availability for tomorrow?
                        You: "You can check all our available time slots for tomorrow at goldtouchmobile.com/providers"
                        
                        User: Do you do deep tissue?
                        You: "Yes, we do! You can check availability and book a deep tissue massage at goldtouchmobile.com/providers"
                        
                        User: How much is a 60-minute massage?
                        You: "Our mobile massage service starts at $150 for 60 minutes. You can see all pricing and book at goldtouchmobile.com/providers"
                        """
                        
                        # Build the conversation history
                        messages = [{"role": "system", "content": system_prompt}]
                        
                        # Add conversation history
                        for msg in conversation_history:
                            messages.append(msg)
                        
                        # Add the current message
                        messages.append({"role": "user", "content": body})
                        
                        # Keep the system prompt and the most recent exchange
                        if len(messages) > 5:  # system + 2 exchanges (4 messages)
                            messages = [messages[0]] + messages[-4:]

                        try:
                            # First try with gpt-4
                            response = client.chat.completions.create(
                                model="gpt-4",
                                messages=messages,
                                max_tokens=150,
                                temperature=0.7,
                            )
                            assistant_response = response.choices[0].message.content.strip()
                            
                        except Exception as e:
                            logger.error(f"GPT-4 Error: {str(e)}")
                            try:
                                # Fallback to gpt-3.5-turbo if gpt-4 fails
                                logger.info("Trying fallback to gpt-3.5-turbo")
                                response = client.chat.completions.create(
                                    model="gpt-3.5-turbo",
                                    messages=messages,
                                    max_tokens=150,
                                    temperature=0.7,
                                )
                                assistant_response = response.choices[0].message.content.strip()
                                logger.info("Successfully used gpt-3.5-turbo as fallback")
                                
                            except Exception as fallback_error:
                                logger.error(f"GPT-3.5 Fallback Error: {str(fallback_error)}")
                                # Provide a helpful fallback message
                                assistant_response = "I'm having trouble connecting to our AI service. Please try again later or visit goldtouchmobile.com/providers for assistance."
                        
                        # Update conversation history with the response (or fallback message)
                        conversation_history.append({"role": "user", "content": body})
                        conversation_history.append({"role": "assistant", "content": assistant_response})
                        
                        # Clean up the response text by removing extra whitespace
                        response_text = ' '.join(assistant_response.split())
                        logger.info(f"Generated response: {response_text}")
                        
                        # Add a 10-second delay to make responses feel more natural
                        time.sleep(10)
                        
                    except Exception as e:
                        logger.error(f"AI response error: {str(e)}", exc_info=True)
                        response_text = "I'm having trouble processing your request. Please try again later or visit goldtouchmobile.com/providers"
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
                    
                    # Update conversation state with the response
                    with MESSAGE_LOCK:
                        conv_key = f"{from_number}:{to_number}"
                        if conv_key in CONVERSATION_STATE:
                            CONVERSATION_STATE[conv_key].update({
                                'last_activity': time.time(),
                                'last_response': response_text,
                                'last_response_time': time.time()
                            })
                    
                    # Always try to send VIP promotion after any response
                    try:
                        conv_key = f"{from_number}:{to_number}"
                        current_time = time.time()
                        
                        with MESSAGE_LOCK:
                            # Schedule VIP message 3 minutes after the last message
                            schedule_vip_message(from_number, to_number, delay_minutes=3)
                            logger.info("Scheduled VIP message for 3 minutes from now")
                    except Exception as vip_error:
                        logger.error(f"Error in VIP promotion logic: {str(vip_error)}", exc_info=True)
                    
                    response_data = {
                        'status': 'success',
                        'message': 'Message processed and responses sent',
                        'to': from_number,
                        'from': to_number,
                        'timestamp': datetime.now(timezone.utc).isoformat()
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
        # Log the raw request data
        logger.info("=== New FluentForms Webhook Request ===")
        logger.info(f"Headers: {dict(request.headers)}")
        logger.info(f"Form Data: {request.form}")
        
        # Get form data
        data = request.get_json(silent=True) or request.form
        logger.info(f"Parsed Data: {data}")
        
        # Extract form fields (adjust these to match your form field names)
        name = data.get('name', 'Customer')
        phone = data.get('phone', '')
        email = data.get('email', 'No email provided')
        service_type = data.get('service_type', 'Massage')  # e.g., 'Swedish', 'Deep Tissue', etc.
        appointment_date = data.get('appointment_date', 'Not specified')
        appointment_time = data.get('appointment_time', 'Not specified')
        location = data.get('location', 'Not specified')
        notes = data.get('notes', 'No additional notes')
        
        # The form name will be used to find the provider
        # Format is assumed to be "Dan Massage Booking Form" -> provider name is "Dan"
        form_name = data.get('form_title', '')
        provider_name = form_name.split(' ')[0] if form_name else ''
        
        logger.info(f"Processing booking - Client: {name}, Phone: {phone}, Form: {form_name}, Provider: {provider_name}")
        
        # Log ClickSend credentials status (without showing actual values)
        logger.info(f"ClickSend username set: {'Yes' if os.getenv('CLICKSEND_USERNAME') else 'No'}")
        logger.info(f"ClickSend API key set: {'Yes' if os.getenv('CLICKSEND_API_KEY') else 'No'}")
        logger.info(f"ClickSend from number: {os.getenv('CLICKSEND_FROM_NUMBER')}")
        
        # Find the provider by name (exact match)
        provider = None
        if provider_name:
            logger.info(f"Looking for provider: {provider_name}")
            for p in sms_manager.providers:
                if p['Name'].lower() == provider_name.lower():
                    provider = p
                    break
        
        if not provider:
            error_msg = f'Provider {provider_name} not found.'
            logger.error(error_msg)
            return jsonify({
                'status': 'error',
                'message': error_msg
            }), 404
        
        provider_phone = provider.get('Phone')
        if not provider_phone:
            error_msg = f'No phone number found for provider {provider_name}.'
            logger.error(error_msg)
            return jsonify({
                'status': 'error',
                'message': error_msg
            }), 400
        
        logger.info(f"Found provider: {provider.get('Name')} - {provider_phone}")
        
        # Format the message to the provider
        provider_msg = (
            f"NEW BOOKING REQUEST\n"
            f"From: {name}\n"
            f"Phone: {phone}\n"
            f"Email: {email}\n"
            f"Service: {service_type}\n"
            f"Date: {appointment_date} at {appointment_time}\n"
            f"Location: {location}\n"
            f"Notes: {notes}"
        )
        
        # Send the message to the provider
        success, error = send_sms(provider_phone, provider_msg)
        
        if success:
            logger.info(f"Booking details sent to provider {provider_name}")
            # Send confirmation to client
            client_msg = (
                f"Thank you for your booking request, {name}! {provider.get('Name')} has been notified "
                f"and will contact you shortly to confirm your {service_type} appointment on {appointment_date} at {appointment_time}."
            )
            send_sms(phone, client_msg)
            
            return jsonify({
                'status': 'success',
                'message': 'Booking details sent to provider',
                'provider': provider.get('Name')
            })
        else:
            error_msg = f'Failed to send booking details to provider: {error}'
            logger.error(error_msg)
            return jsonify({
                'status': 'error',
                'message': error_msg
            }), 500
            
    except Exception as e:
        logger.error(f"Error in webhook: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': f'Internal server error: {str(e)}'
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

@app.route('/hubspot-webhook', methods=['POST', 'GET'])
@limiter.limit("100 per hour")  # Rate limiting for webhook
@limiter.limit("1000 per day")
def hubspot_webhook():
    """
    Handle HubSpot webhook events
    """
    logger = logging.getLogger(__name__)
    
    # Log the incoming request for debugging
    logger.info("\n=== HUBSPOT WEBHOOK RECEIVED ===")
    logger.info(f"Method: {request.method}")
    logger.info(f"Headers: {dict(request.headers)}")
    
    # Handle webhook verification (HubSpot sends a GET request to verify the endpoint)
    if request.method == 'GET':
        hubspot_challenge = request.args.get('hub.challenge')
        if hubspot_challenge:
            logger.info("HubSpot webhook verification successful")
            return hubspot_challenge, 200, {'Content-Type': 'text/plain'}
        return "Webhook verification failed", 400
    
    # Handle webhook events (POST request)
    try:
        # Get the webhook payload
        payload = request.json
        logger.info(f"Webhook payload: {json.dumps(payload, indent=2)}")
        
        # Verify the webhook signature (if configured)
        if not verify_hubspot_signature(request):
            logger.warning("Invalid webhook signature")
            return jsonify({'status': 'error', 'message': 'Invalid signature'}), 401
        
        # Process the webhook event
        event_type = payload.get('subscriptionType')
        
        if event_type == 'contact.creation':
            # Handle contact creation event
            contact_id = payload.get('objectId')
            logger.info(f"New contact created in HubSpot: {contact_id}")
            
            # Here you can add logic to handle the new contact
            # For example, send a welcome message or update internal systems
            
            return jsonify({'status': 'success', 'message': 'Webhook processed'}), 200
            
        else:
            logger.info(f"Unhandled webhook event type: {event_type}")
            return jsonify({'status': 'success', 'message': 'Event type not handled'}), 200
            
    except Exception as e:
        error_msg = f"Error processing webhook: {str(e)}"
        logger.error(f"{error_msg}\n{traceback.format_exc()}")
        return jsonify({'status': 'error', 'message': error_msg}), 500

def verify_hubspot_signature(request) -> bool:
    """
    Verify the HubSpot webhook signature
    
    Args:
        request: The Flask request object
        
    Returns:
        bool: True if signature is valid, False otherwise
    """
    client_secret = os.getenv('HUBSPOT_CLIENT_SECRET')
    if not client_secret:
        logger.warning("No HUBSPOT_CLIENT_SECRET configured, skipping signature verification")
        return True  # Skip verification if no secret is configured
    
    signature = request.headers.get('X-HubSpot-Signature')
    if not signature:
        logger.warning("No X-HubSpot-Signature header found")
        return False
    
    # Get the request body as bytes for signature verification
    request_data = request.get_data()
    
    try:
        # Verify the signature using HMAC-SHA256
        import hmac
        import hashlib
        
        # Create a new hash of the request body using the client secret
        expected_signature = hmac.new(
            client_secret.encode('utf-8'),
            request_data,
            hashlib.sha256
        ).hexdigest()
        
        # Compare the signatures
        return hmac.compare_digest(signature, expected_signature)
        
    except Exception as e:
        logger.error(f"Error verifying webhook signature: {str(e)}")
        return False

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

def send_vip_message(from_number, to_number):
    """Send the VIP message to the specified number"""
    try:
        vip_message = "Many clients choose our VIP membership for faster bookings and extra perks. Plans start at $25/month — and every dollar goes toward your sessions, so your membership always pays for itself. goldtouchmobile.com/vip"
        logger.info(f"[VIP] Attempting to send VIP message to {from_number}")
        
        # Log the exact message being sent
        logger.info(f"[VIP] Message content: {vip_message}")
        
        send_success, send_message = send_sms(
            to=from_number,
            body=vip_message,
            from_number=to_number
        )
        
        if send_success:
            logger.info(f"[VIP] Successfully sent VIP promotion message to {from_number}")
            return True
        else:
            logger.error(f"[VIP] Failed to send VIP promotion message to {from_number}: {send_message}")
            return False
    except Exception as e:
        logger.error(f"[VIP] Error in send_vip_message for {from_number}: {e}", exc_info=True)
        return False

def process_vip_messages():
    """Stub for compatibility - not used with timer-based approach"""
    logger.info("VIP message worker is not used with timer-based approach")
    while True:
        time.sleep(3600)  # Sleep for a long time to reduce CPU usage

# Simple uptime monitor that pings itself every 5 minutes
def keep_alive():
    while True:
        try:
            # Ping the server to keep it alive
            requests.get('https://sms-yd7t.onrender.com/ping')
            logger.info("Keep-alive ping sent")
        except Exception as e:
            logger.error(f"Error in keep-alive: {e}")
        time.sleep(300)  # Ping every 5 minutes

# Start background workers when the app starts
if not os.environ.get('WERKZEUG_RUN_MAIN'):
    # Start the VIP message worker
    threading.Thread(target=process_vip_messages, daemon=True).start()
    # Start the keep-alive thread
    threading.Thread(target=keep_alive, daemon=True).start()

# Test endpoint to verify OpenAI connectivity
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
