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
MAX_FREE_DAILY = 60
MAX_FREE_MONTHLY = 100
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

def build_enhanced_prompt(base_prompt, params):
    """Enhance prompt with style and technical directives"""
    prompt_parts = [base_prompt]
    
    # Lighting control
    if params.get('lighting'):
        prompt_parts.append(f"{params['lighting']} lighting")
    
    # Camera angle
    if params.get('angle'):
        prompt_parts.append(f"{params['angle']} angle")
    
    # Style modifiers
    if params.get('style') == 'vintage':
        intensity = params.get('vintage_intensity', 0.5)
        prompt_parts.append(f"35mm film, grain, faded colors (intensity: {intensity})")
    elif params.get('style') == 'classic':
        prompt_parts.append("oil painting, brush strokes, renaissance style")
    
    # HDR effect
    if params.get('hdr', False):
        prompt_parts.append("HDR, ultra-detailed, 8k")
    
    # Negative prompt
    if params.get('negative_prompt'):
        prompt_parts.append(f"| NEGATIVE: {params['negative_prompt']}")
    
    return ", ".join(prompt_parts)

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
        user_input = request.json or {}

    # Authentication and rate limiting
    api_key = (request.headers.get('X-API-Key') or '').strip()
    valid_keys = [k.strip() for k in API_KEYS]
    is_paid_user = api_key and api_key in valid_keys
    ip = request.remote_addr
    
    with lock:
        if is_paid_user:
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
        # Base parameters
        params = {
            "width": user_input.get('width', 1280),
            "height": user_input.get('height', 720),
            "seed": user_input.get('seed', int.from_bytes(os.urandom(2), "big")),
            "model": user_input.get('model', 'flux'),
            "nologo": not user_input.get('add_logo', False),
            "steps": user_input.get('steps', 50),
            "cfg_scale": user_input.get('cfg_scale', 7.5),
            "sampler": user_input.get('sampler', 'k_euler')
        }

        # Enhanced prompt building
        enhanced_prompt = build_enhanced_prompt(
            user_input['prompt'],
            {
                'lighting': user_input.get('lighting'),
                'angle': user_input.get('angle'),
                'hdr': user_input.get('hdr', False),
                'style': user_input.get('style'),
                'vintage_intensity': user_input.get('vintage_intensity', 0.5),
                'negative_prompt': user_input.get('negative_prompt')
            }
        )

        # Batch processing
        batch_size = min(4, max(1, int(user_input.get('batch_size', 1))))
        batch_count = min(5, max(1, int(user_input.get('batch_count', 1))))
        
        # Upscaling
        if user_input.get('upscale', False):
            params["upscale"] = "true"
            params["upscale_factor"] = min(4.0, max(1.0, float(user_input.get('upscale_factor', 2.0))))

        # Logo branding
        if user_input.get('add_logo', False) and user_input.get('logo_url'):
            params["logo_url"] = user_input['logo_url']
            params["logo_opacity"] = min(1.0, max(0.1, float(user_input.get('logo_opacity', 0.7))))

        # Encode the final prompt
        encoded_prompt = urllib.parse.quote(enhanced_prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        
        # Process batches
        images = []
        for _ in range(batch_count):
            response = requests.get(url, params={**params, "batch_size": batch_size}, timeout=300)
            response.raise_for_status()
            if batch_size == 1:
                images.append(response.content)
            else:
                images.extend(response.json().get('images', []))

        # Return response
        if len(images) == 1:
            return Response(
                images[0],
                mimetype='image/jpeg',
                headers={
                    'X-RateLimit-Type': user_type,
                    'X-RateLimit-Remaining': str(MAX_PAID_MONTHLY - usage_tracker[api_key]['monthly_count']) if user_type == 'paid' else f"{MAX_FREE_DAILY - usage_tracker[ip]['daily_count']}/{MAX_FREE_MONTHLY - usage_tracker[ip]['monthly_count']}",
                    'Cache-Control': 'no-store'
                }
            )
        else:
            return jsonify({
                "images": [img.decode('latin1') if isinstance(img, bytes) else img for img in images],
                "metadata": params,
                "credits_used": batch_count * batch_size
            })

    except Exception as e:
        app.logger.error(f"Image generation failed: {str(e)}")
        return jsonify({'error': 'Image generation failed', 'details': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)
