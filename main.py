import os
import json
import time
import logging
import requests
from flask import Flask, request, jsonify
from sms_booking import SMSBookingManager, send_sms

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Set your tokens as environment variables or replace with your actual values
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', 'MY_OPENAI_API_KEY')
FB_PAGE_ACCESS_TOKEN = os.getenv('FB_PAGE_ACCESS_TOKEN', 'MY_PAGE_ACCESS_TOKEN')

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
        logger.info(f"Incoming SMS webhook data: {request.form}")
        
        # Parse incoming message (ClickSend format)
        from_number = request.form.get('from')
        body = request.form.get('message', '').strip()
        
        if not from_number:
            logger.error("No 'from' number in webhook data")
            return jsonify({'error': 'Missing from number'}), 400
            
        logger.info(f"Received SMS from {from_number}: {body}")
        
        # Check if this is a provider response to a booking (e.g., "YES" or "NO")
        if body.upper() in ['YES', 'NO']:
            # Extract booking_id if available (you may need to track this in a database)
            booking_id = request.form.get('custom_string')  # Or parse from body
            if booking_id:
                logger.info(f"Processing provider response for booking {booking_id}")
                sms_manager.handle_provider_response(booking_id, from_number, body)
            else:
                logger.warning("Received YES/NO but no booking_id found")
        else:
            # Handle other inbound messages (e.g., customer inquiries)
            logger.info(f"Forwarding message to OpenAI: {body}")
            # Add your OpenAI response logic here if needed
            
        return ('', 204)  # Return 204 No Content to acknowledge receipt
        
    except Exception as e:
        logger.error(f"Error in sms_webhook: {str(e)}", exc_info=True)
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
