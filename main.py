import os
import json
import requests
from flask import Flask, request, jsonify
from sms_booking import SMSBookingManager

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

# SMS webhook to handle provider replies
@app.route('/sms-webhook', methods=['POST'])
def sms_webhook():
    # Example assumes Twilio POST format
    from_number = request.form.get('From')
    body = request.form.get('Body', '')
    booking_id = request.form.get('booking_id')  # You may need to track booking_id via session or DB
    # You may need to match provider by phone number
    # For demo, just log the reply
    print(f"Received SMS from {from_number}: {body}")
    # Process provider response (YES/NO)
    if booking_id:
        sms_manager.handle_provider_response(booking_id, from_number, body)
    return ('', 204)

# Webhook endpoint for Fluent Forms Pro integration
@app.route('/fluentforms-webhook', methods=['POST'])
def fluentforms_webhook():
    # Try to get JSON, fallback to form if needed
    data = request.get_json(silent=True) or request.form
    name = data.get('name', 'Customer')
    phone = data.get('phone', '')
    message = data.get('message', '')

    # Compose a user message for OpenAI (customize as needed)
    user_message = f"Form submission from {name} ({phone}): {message}"

    # Use the same OpenAI logic as Messenger/SMS
    openai_url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ],
        "max_tokens": 200,
        "temperature": 0.85
    }
    openai_resp = requests.post(openai_url, headers=headers, json=payload)
    if openai_resp.status_code != 200:
        return jsonify({'error': 'OpenAI API error', 'details': openai_resp.text}), 500
    gpt_reply = openai_resp.json()['choices'][0]['message']['content'].strip()

    # Optionally: send SMS, email, or trigger booking logic here
    # send_sms(phone, gpt_reply)

    return jsonify({'reply': gpt_reply}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
