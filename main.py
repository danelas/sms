import os
import json
import time
import logging
import openai
import requests
from flask import Flask, request, jsonify
from sms_booking import SMSBookingManager, send_sms
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

# Initialize OpenAI client
openai_api_key = os.getenv('OPENAI_API_KEY')
if not openai_api_key:
    raise ValueError("OPENAI_API_KEY environment variable not set")

client = OpenAI(api_key=openai_api_key)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Set your tokens as environment variables or replace with your actual values
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', 'MY_OPENAI_API_KEY')
FB_PAGE_ACCESS_TOKEN = os.getenv('FB_PAGE_ACCESS_TOKEN', 'MY_PAGE_ACCESS_TOKEN')

# Root endpoint to confirm the server is running
@app.route('/')
def index():
    return """
    <h1>Gold Touch Massage SMS Service</h1>
    <p>Server is running! </p>
    <h3>Test Endpoints:</h3>
    <ul>
        <li><a href="/test-webhook" target="_blank">Test Webhook</a> - Check if the webhook is working</li>
        <li><code>POST /sms-webhook</code> - Handle incoming SMS (test with cURL)</li>
        <li><code>GET /test-sms?to=+1234567890</code> - Send a test SMS (replace with your number)</li>
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


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        entry = data['entry'][0]
        messaging = entry['messaging'][0]
        sender_id = messaging['sender']['id']
        message_text = messaging['message']['text']
    except (KeyError, IndexError, TypeError):
        return jsonify({'error': 'Invalid payload structure'}), 400

    # Call OpenAI ChatGPT
    openai_url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message_text}
        ],
        "max_tokens": 200,
        "temperature": 0.85
    }
    openai_resp = requests.post(openai_url, headers=headers, json=payload)
    if openai_resp.status_code != 200:
        return jsonify({'error': 'OpenAI API error', 'details': openai_resp.text}), 500
    gpt_reply = openai_resp.json()['choices'][0]['message']['content'].strip()

    # Send reply back to Facebook Messenger
    fb_url = f"https://graph.facebook.com/v12.0/me/messages?access_token={FB_PAGE_ACCESS_TOKEN}"
    fb_payload = {
        "recipient": {"id": sender_id},
        "message": {"text": gpt_reply}
    }
    fb_headers = {"Content-Type": "application/json"}
    fb_resp = requests.post(fb_url, headers=fb_headers, json=fb_payload)

    if fb_resp.status_code != 200:
        return jsonify({'error': 'Facebook Send API error', 'details': fb_resp.text}), 500

    return jsonify({'reply': gpt_reply}), 200

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
@app.route('/sms-webhook', methods=['POST'])
def sms_webhook():
    try:
        # Log raw incoming request data for debugging
        logger.info("=== Incoming SMS Webhook ===")
        logger.info(f"Headers: {dict(request.headers)}")
        logger.info(f"Form Data: {dict(request.form)}")
        logger.info(f"JSON Data: {request.get_json(silent=True) or 'No JSON data'}")
        
        # Parse incoming message (ClickSend format)
        from_number = request.form.get('from') or request.form.get('From')
        to_number = request.form.get('to') or request.form.get('To')  # The ClickSend number that received the message
        body = request.form.get('message', request.form.get('Body', '')).strip()
        
        if not from_number or not to_number:
            logger.error(f"Missing 'from' or 'to' number in webhook data. From: {from_number}, To: {to_number}")
            return jsonify({'error': 'Missing from/to number'}), 400
            
        logger.info(f"📱 Received SMS from {from_number} to {to_number}: {body}")
        
        # Log all form fields for debugging
        for key, value in request.form.items():
            logger.info(f"Form field - {key}: {value}")
        
        # Check if this is a provider response to a booking (e.g., "YES" or "NO")
        if body.upper() in ['YES', 'NO']:
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
                        
                        openai_response = client.chat.completions.create(
                            model="gpt-3.5-turbo",
                            messages=[
                                {"role": "system", "content": "You are a friendly massage therapist assistant. Keep responses short, warm, and conversational."},
                                {"role": "user", "content": prompt}
                            ],
                            max_tokens=60,
                            temperature=0.8
                        )
                        response_text = openai_response.choices[0].message.content.strip('"\'').strip()
                        
                    except Exception as e:
                        logger.error(f"AI response error: {str(e)}")
                        response_text = "Thanks for your message! How can I help you today? 😊"
                        
                logger.info(f"Generated response: {response_text}")
                
            except Exception as e:
                logger.error(f"Error generating AI response: {str(e)}", exc_info=True)
                # More engaging default message with booking link
                response_text = """Hi there! 😊 Thanks for your message! 

You can book a massage 24/7 at: goldtouchmobile.com/providers

Or just reply with your preferred day/time and we'll help you out! 💆‍♀️✨"""
                logger.info(f"Using fallback response")
                
                # Log the full error for debugging
                import traceback
                logger.error(f"Full error: {traceback.format_exc()}")
            
            # Send the response back to the sender
            send_sms(to=from_number, body=response_text, from_number=to_number)
            logger.info(f"Sent response to {from_number}")
            
        return ('', 204)  # Return 204 No Content to acknowledge receipt
        
    except Exception as e:
        logger.error(f"❌ Error in sms_webhook: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

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
    """Test endpoint to verify webhook connectivity.
    
    This endpoint can be tested in multiple ways:
    1. Visit in a browser (GET request)
    2. Send a POST request with form data
    3. Send a POST request with JSON data
    """
    # Log the incoming request
    logger.info("=== Test Webhook Called ===")
    logger.info(f"Method: {request.method}")
    logger.info(f"Headers: {dict(request.headers)}")
    
    # Parse form data if it exists
    form_data = {}
    if request.form:
        form_data = dict(request.form)
    
    # Parse JSON data if it exists
    json_data = {}
    if request.is_json:
        json_data = request.get_json() or {}
    
    # Log the data
    logger.info(f"Form Data: {form_data}")
    logger.info(f"JSON Data: {json_data}")
    logger.info("==========================")
    
    # Return a response based on the request method
    if request.method == 'GET':
        return """
        <h1>Webhook Test Endpoint</h1>
        <p>This is a test endpoint to verify webhook connectivity.</p>
        <h3>Test with cURL:</h3>
        <pre>
        # Send form data
        curl -X POST https://sms-yd7t.onrender.com/test-webhook \
          -d "test=123&message=hello"
        
        # Send JSON data
        curl -X POST https://sms-yd7t.onrender.com/test-webhook \
          -H "Content-Type: application/json" \
          -d '{"test": 123, "message": "hello"}'
        </pre>
        """
    else:
        return jsonify({
            'status': 'success',
            'message': 'Webhook is working!',
            'method': request.method,
            'form_data': form_data,
            'json_data': json_data,
            'headers': dict(request.headers)
        }), 200

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
