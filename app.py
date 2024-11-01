from flask import Flask, jsonify, request
import requests
from flask_caching import Cache
import mysql.connector
import os
from dotenv import load_dotenv
import bcrypt
import jwt
import datetime
from functools import wraps
from flask_cors import CORS


# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)
# Initialize Flask app and caching
cache = Cache(app, config={'CACHE_TYPE': 'simple'})  # Simple in-memory cache
API_KEY = os.getenv('API_KEY')

# Database connection function
def get_db_connection():
    connection = mysql.connector.connect(
        host='localhost',
        user='root',              # Update with your XAMPP MySQL user
        password='',              # Default XAMPP password is empty
        database='nutrition_db'   # Replace with your database name
    )
    return connection

# User authentication and registration functions
def get_user_by_id(user_id):
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute('SELECT * FROM Users WHERE id = %s', (user_id,))
    user = cursor.fetchone()
    cursor.close()
    connection.close()
    return user

def log_api_usage(user_id, endpoint):
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute('INSERT INTO API_Usage (user_id, endpoint, timestamp) VALUES (%s, %s, NOW())',
                   (user_id, endpoint))
    connection.commit()
    cursor.close()
    connection.close()

def update_user(user):
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute('UPDATE Users SET api_calls_remaining = %s, last_reset = %s WHERE id = %s',
                   (user['api_calls_remaining'], user['last_reset'], user['id']))
    connection.commit()
    cursor.close()
    connection.close()

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute('INSERT INTO Users (username, password, subscription_plan) VALUES (%s, %s, %s)', (username, hashed, 'free'))
    connection.commit()
    cursor.close()
    connection.close()
    return jsonify({'message': 'User registered successfully!'}), 201

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute('SELECT * FROM Users WHERE username = %s', (username,))
    user = cursor.fetchone()

    if user and bcrypt.checkpw(password.encode('utf-8'), user[2].encode('utf-8')):
        token = jwt.encode({'user_id': user[0], 'exp': datetime.datetime.utcnow() + datetime.timedelta(days=1)}, 'your_secret_key', algorithm='HS256')
        return jsonify({'token': token})
    
    return jsonify({'message': 'Invalid credentials!'}), 401

# Rate limiting middleware
def rate_limit(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'message': 'Missing token!'}), 401
        try:
            user_id = jwt.decode(token, 'your_secret_key', algorithms=['HS256'])['user_id']
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token has expired!'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': 'Invalid token!'}), 401

        user = get_user_by_id(user_id)
        
        # Check remaining API calls
        if user[5] <= 0:  # api_calls_remaining
            return jsonify({'message': 'Rate limit exceeded. Try again tomorrow.'}), 429

        log_api_usage(user[0], request.path)  # Log API call
        user_calls_remaining = user[5] - 1  # Decrease count
        last_reset = user[6]  # Get last reset date

        # Reset daily
        if last_reset < datetime.datetime.now() - datetime.timedelta(days=1):
            user_calls_remaining = 5  # Reset to 5
            last_reset = datetime.datetime.now()

        # Update user in DB
        update_user({'id': user[0], 'api_calls_remaining': user_calls_remaining, 'last_reset': last_reset})

        return f(*args, **kwargs)
    return decorated_function

# API endpoints
@app.route('/foods', methods=['GET'])
@rate_limit
@cache.cached(timeout=300, query_string=True)  # Cache for 5 minutes
def search_foods():
    query = request.args.get('query')
    response = requests.get(f'https://api.nal.usda.gov/fdc/v1/foods/search?query={query}&api_key={API_KEY}')
    data = response.json()
    return jsonify(data)

@app.route('/foods/<int:fdcId>', methods=['GET'])
@rate_limit
@cache.cached(timeout=300)  # Cache food details for 5 minutes
def get_food_details(fdcId):
    response = requests.get(f'https://api.nal.usda.gov/fdc/v1/food/{fdcId}?api_key={API_KEY}')
    food_data = response.json()
    return jsonify(food_data)

@app.route('/user-foods', methods=['POST'])
@rate_limit
def add_user_food():
    data = request.json
    description = data.get('description')
    ingredients = data.get('ingredients')
    serving_size = data.get('servingSize')
    nutrients = data.get('nutrients')

    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.execute('INSERT INTO UserFoods (description, ingredients, servingSize, nutrients) VALUES (%s, %s, %s, %s)',
                   (description, ingredients, serving_size, nutrients))
    connection.commit()
    cursor.close()
    connection.close()
    return jsonify({'message': 'Food item added successfully!'}), 201

# Run the app
if __name__ == '__main__':
    app.run(debug=True)
