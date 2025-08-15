from flask import Flask, request, Response, jsonify
import requests
import urllib.parse
import threading
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

# Load environment variables

load_dotenv()

app = Flask(__name__)

# Configuration
MAX_FREE_DAILY = 3
MAX_FREE_MONTHLY = 30
MAX_PAID_MONTHLY = 200
API_KEYS = os.getenv('API_KEYS', '').split(',')
RESET_INTERVAL = 24 * 3600  # Daily reset in seconds

# Rate tracking
usage_tracker = {}
lock = threading.Lock()

def reset_usage():
    """Periodically reset usage counts"""
    with lock:
        now = datetime.now()
        for key, usage in list(usage_tracker.items()):
            # Reset daily counts
            if 'daily_reset' not in usage or (now - usage['daily_reset']).days >= 1:
                usage['daily_count'] = 0
                usage['daily_reset'] = now
            
            # Reset monthly counts
            if 'monthly_reset' not in usage or now.month != usage['monthly_reset'].month:
                usage['monthly_count'] = 0
                usage['monthly_reset'] = now

# Start reset thread
def reset_scheduler():
    while True:
        reset_usage()
        threading.Event().wait(RESET_INTERVAL)

threading.Thread(target=reset_scheduler, daemon=True).start()

@app.route('/generate/<path:prompt>')
@app.route('/generate', methods=['POST'])
def generate_image(prompt=None):
    # Handle both GET (URL) and POST requests
    if prompt:
        # GET request with prompt in URL
        decoded_prompt = urllib.parse.unquote_plus(prompt)
        user_input = {'prompt': decoded_prompt, **request.args}
    else:
        # POST request
        user_input = request.json

    # Authentication and rate limiting
    # Replace this:
    api_key = request.headers.get('X-API-Key')

# With this STRICT check:
    api_key = (request.headers.get('X-API-Key') or '').strip()
    valid_keys = [k.strip() for k in API_KEYS]
    is_paid_user = api_key and api_key in valid_keys
    ip = request.remote_addr
    
    with lock:
        if api_key and api_key in API_KEYS:
            # Paid user logic
            if api_key not in usage_tracker:
                usage_tracker[api_key] = {
                    'monthly_count': 0,
                    'monthly_reset': datetime.now()
                }
            
            if usage_tracker[api_key]['monthly_count'] >= MAX_PAID_MONTHLY:
                return jsonify({
                    'error': 'Monthly limit exceeded',
                    'limit': MAX_PAID_MONTHLY,
                    'reset': usage_tracker[api_key]['monthly_reset'].strftime('%Y-%m-%d')
                }), 429
            
            usage_tracker[api_key]['monthly_count'] += 1
            user_type = 'paid'
        else:
            # Free user logic
            if ip not in usage_tracker:
                usage_tracker[ip] = {
                    'daily_count': 0,
                    'monthly_count': 0,
                    'daily_reset': datetime.now(),
                    'monthly_reset': datetime.now()
                }
            
            if usage_tracker[ip]['daily_count'] >= MAX_FREE_DAILY:
                return jsonify({
                    'error': 'Daily free limit exceeded',
                    'limit': MAX_FREE_DAILY,
                    'reset': (usage_tracker[ip]['daily_reset'] + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
                }), 429
            
            if usage_tracker[ip]['monthly_count'] >= MAX_FREE_MONTHLY:
                return jsonify({
                    'error': 'Monthly free limit exceeded',
                    'limit': MAX_FREE_MONTHLY,
                    'reset': usage_tracker[ip]['monthly_reset'].strftime('%Y-%m-%d')
                }), 429
            
            usage_tracker[ip]['daily_count'] += 1
            usage_tracker[ip]['monthly_count'] += 1
            user_type = 'free'

    # Generate image
    try:
        params = {
            "width": user_input.get('width', 1280),
            "height": user_input.get('height', 720),
            "seed": user_input.get('seed', 42),
            "nologo": user_input.get('nologo', True),
            "model": user_input.get('model', 'flux'),
            "prompt": user_input['prompt']
        }

        encoded_prompt = urllib.parse.quote(params.pop('prompt'))
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        response = requests.get(url, params=params, timeout=300)
        response.raise_for_status()

        # Return image with rate limit headers
        return Response(
            response.content,
            mimetype='image/jpeg',
            headers={
                'X-RateLimit-Type': user_type,
                'X-RateLimit-Remaining': 
                    str(MAX_PAID_MONTHLY - usage_tracker[api_key]['monthly_count']) if user_type == 'paid'
                    else f"{MAX_FREE_DAILY - usage_tracker[ip]['daily_count']}/{MAX_FREE_MONTHLY - usage_tracker[ip]['monthly_count']}",
                'Cache-Control': 'no-store'
            }
        )

    except Exception as e:
        app.logger.error(f"Image generation failed: {str(e)}")
        return jsonify({'error': 'Image generation failed', 'details': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
