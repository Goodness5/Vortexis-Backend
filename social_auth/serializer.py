from rest_framework import serializers
from .utils import Google, register_social_user, Github
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed

class GoogleSocialAuthSerializer(serializers.Serializer):
    access_token = serializers.CharField()

    def validate(self, attrs):
        access_token = attrs.get('access_token')
        try:
            idinfo = Google.validate(access_token)
        except Exception as e:
            raise AuthenticationFailed('Invalid token')
        if idinfo['aud'] != settings.GOOGLE_CLIENT_ID:
            raise AuthenticationFailed('Invalid client id')
        email = idinfo['email']
        first_name = idinfo.get('given_name', '')
        last_name = idinfo.get('family_name', '')
        provider = 'google'
        # Use email as username for Google auth
        user_data = register_social_user(provider, email, email, first_name, last_name)
        attrs['user_data'] = user_data
        return attrs
    
class GithubSocialAuthSerializer(serializers.Serializer):
    code = serializers.CharField()

    def validate(self, attrs):
        code = attrs.get('code')
        access_token = Github.get_token(code)
        if access_token:
            github_user = Github.get_user_details(access_token)
            full_name = github_user.get('name', '')
            email = github_user.get('email')
            if not email:
                raise AuthenticationFailed(detail='Email is required')
            names = full_name.split(' ') if full_name else ['', '']
            if len(names) > 1:
                first_name = names[0]
                last_name = ' '.join(names[1:])
            else:
                first_name = names[0] if names else ''
                last_name = ''
            username = github_user.get('login')
            provider = 'github'
            user_data = register_social_user(provider, username, email, first_name, last_name)
            attrs['user_data'] = user_data
            return attrs
        else:
            raise AuthenticationFailed(detail='Invalid code')