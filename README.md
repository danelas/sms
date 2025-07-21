# Facebook Messenger + ChatGPT Webhook

This is a ready-to-deploy Python Flask webhook that connects Facebook Messenger to OpenAI's ChatGPT (gpt-3.5-turbo or gpt-4).

## Features
- Receives messages from Facebook Messenger via webhook
- Sends user messages to OpenAI ChatGPT with a business-specific system prompt
- Replies to users via Facebook Messenger Send API

## Setup
1. **Clone or copy this directory.**
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Set your API keys:**
   - Set environment variables `OPENAI_API_KEY` and `FB_PAGE_ACCESS_TOKEN`
   - Or replace the placeholder values in `main.py`
4. **Run the server:**
   ```bash
   python main.py
   ```
5. **Expose your local server to the internet** (for Facebook webhook validation):
   - Use [ngrok](https://ngrok.com/) or similar:
     ```bash
     ngrok http 5000
     ```
6. **Set your webhook URL in Facebook Developer Console:**
   - Use the ngrok HTTPS URL + `/webhook` path

## Notes
- Replace the placeholders for API keys before deploying.
- You can deploy this app to Pipedream, Railway, Render, or any platform that supports Python webhooks.

## Security
- Never commit your real API keys to public repositories.

---
Questions? Just ask!
