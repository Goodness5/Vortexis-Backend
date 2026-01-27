from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import requests
from django.conf import settings
from accounts.models import User
from django.contrib.auth import authenticate
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed


class Google:

    @staticmethod
    def validate(access_token):
        try:
            idinfo = id_token.verify_oauth2_token(access_token, google_requests.Request(), settings.GOOGLE_CLIENT_ID)

            if idinfo['iss'] not in ['accounts.google.com', 'https://accounts.google.com']:
                raise ValueError('Wrong issuer.')

            return idinfo
        except Exception as e:
            raise AuthenticationFailed('Invalid or expired token')
        

def register_social_user(provider, username, email, first_name, last_name):
    user = User.objects.filter(email=email).first()
    if user:
        # User exists - link the social account to existing account
        # Update user info if needed (but preserve original username if it's different)
        # Update first_name and last_name if they're empty or if social provider has better data
        if not user.first_name or (first_name and first_name.strip()):
            user.first_name = first_name or user.first_name
        if not user.last_name or (last_name and last_name.strip()):
            user.last_name = last_name or user.last_name
        
        # Mark user as verified if they weren't before (social auth is inherently verified)
        if not user.is_verified:
            user.is_verified = True
        
        # If user was created with email/password, we can still allow social login
        # We don't change auth_provider to preserve the original method, but allow both
        # Alternatively, we can track that they can use both methods
        user.save()
        return get_user_tokens(user)
    else:
        # New user - check for username conflicts
        original_username = username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{original_username}_{counter}"
            counter += 1
        
        # Create new user with social auth
        new_user = User.objects.create_user(
            email=email, 
            username=username, 
            first_name=first_name, 
            last_name=last_name or '', 
            password=settings.SOCIAL_AUTH_PASSWORD, 
            auth_provider=provider
        )
        new_user.is_verified = True
        new_user.is_participant = True  # Set default role
        new_user.save()
        
        # Create profile for new user
        from accounts.models import Profile
        Profile.objects.get_or_create(user=new_user)
        
        return get_user_tokens(new_user)

def get_user_tokens(user):
    """Generate tokens for a user without requiring password authentication."""
    user_tokens = user.tokens()
    return {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'full_name': (user.first_name + ' ' + user.last_name).strip() or user.username,
        'access_token': str(user_tokens['access']),
        'refresh_token': str(user_tokens['refresh'])
    }

def login_social_user(username):
    """Deprecated: Use get_user_tokens instead for social auth."""
    login_user = authenticate(username=username, password=settings.SOCIAL_AUTH_PASSWORD)
    if not login_user:
        raise AuthenticationFailed('Invalid credentials')
    return get_user_tokens(login_user)


class Github:
    @staticmethod
    def get_token(code):
        payload = {
            'client_id': settings.GITHUB_CLIENT_ID,
            'client_secret': settings.GITHUB_CLIENT_SECRET,
            'code': code
        }
        headers = {
            'Accept': 'application/json'
        }
        response = requests.post('https://github.com/login/oauth/access_token', data=payload, headers=headers)
        return response.json().get('access_token')
    
    @staticmethod
    def get_user_details(access_token):
        try:
            headers = {
                'Authorization': f'Bearer {access_token}'
            }
            response = requests.get('https://api.github.com/user', headers=headers)
            return response.json()
        except Exception as e:
            raise AuthenticationFailed('Invalid or expired token')
    