import random
import string
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone
from datetime import timedelta
from notifications.services import NotificationService

from .models import User, OTP

def generate_otp(user):
    """
    Generate a 6-digit OTP and store it in the database.
    Invalidates any previous unused OTPs for the user.
    """
    # Invalidate any previous unused OTPs for this user
    OTP.objects.filter(user=user, is_used=False).update(is_used=True)
    
    # Generate a 6-digit random OTP
    code = ''.join(random.choices(string.digits, k=6))
    
    # Create and save the OTP
    otp_obj = OTP.objects.create(
        user=user,
        code=code,
        expires_at=timezone.now() + timedelta(minutes=10)  # OTP expires in 10 minutes
    )
    
    return code

def verify_otp(user, code):
    """
    Verify an OTP code for a user.
    Returns True if the OTP is valid, False otherwise.
    """
    if not user or not code:
        return False
    
    # Get the most recent unused OTP for this user
    otp_obj = OTP.objects.filter(
        user=user,
        code=code,
        is_used=False
    ).order_by('-created_at').first()
    
    if not otp_obj:
        return False
    
    # Check if OTP is expired
    if otp_obj.is_expired():
        return False
    
    # Mark OTP as used
    otp_obj.is_used = True
    otp_obj.save()
    
    return True


def send_otp_mail(email):
    user = User.objects.get(email=email)
    otp = generate_otp(user)

    subject = 'Vortexis Verification OTP'
    message = f'Hi {user.first_name},\n\nThank you for signing up on Vortexis. Please use the following OTP to verify your account.\n\nOTP: {otp}\n\nIf you did not sign up on Vortexis, please ignore this email.\n\nRegards,\nVortexis Team'

    NotificationService.send_notification(
        user=user,
        title=subject,
        message=message,
        category='account',
        priority='high',
        send_email=True,
        send_in_app=False,
        data={'otp': otp, 'action': 'verify_account'}
    )


def send_password_reset_email(user, reset_token, request):
    # Get frontend URL from request origin
    origin = request.META.get('HTTP_ORIGIN') or f"http://{request.get_host()}"
    reset_url = f"{origin}/reset-password?token={reset_token.token}"

    subject = 'Password Reset - Vortexis'
    message = f'''Hi {user.first_name},

You have requested to reset your password for your Vortexis account.

Please click the following link to reset your password:

{reset_url}

This link will expire in 1 hour.

If you did not request a password reset, please ignore this email and your password will remain unchanged.

For security reasons, this reset link can only be used once.

Regards,
Vortexis Team'''

    NotificationService.send_notification(
        user=user,
        title=subject,
        message=message,
        category='security',
        priority='high',
        send_email=True,
        send_in_app=True,
        action_url=reset_url,
        action_text='Reset Password',
        data={'reset_token': reset_token.token, 'action': 'password_reset'}
    )


def send_judge_invitation_email(email_address, hackathon, invitation_token, request):
    # Get frontend URL from request origin
    origin = request.META.get('HTTP_ORIGIN') or f"http://{request.get_host()}"
    accept_url = f"{origin}/judge-invitation?token={invitation_token}"

    subject = f'Invitation to Judge {hackathon.title}'
    message = f'''Hello,

You have been invited to judge the hackathon '{hackathon.title}'.

Hackathon Details:
- Title: {hackathon.title}
- Description: {hackathon.description}
- Venue: {hackathon.venue}
- Start Date: {hackathon.start_date}
- End Date: {hackathon.end_date}

To accept this invitation, please click the following link:

{accept_url}

If you don't have an account yet, you'll be redirected to create one first.

This invitation will expire in 7 days.

If you did not expect this invitation, please ignore this email.

Regards,
Vortexis Team'''

    # Try to get user by email, create a temporary notification if user exists
    try:
        user = User.objects.get(email=email_address)
        NotificationService.send_notification(
            user=user,
            title=subject,
            message=message,
            category='account',
            priority='high',
            send_email=True,
            send_in_app=True,
            action_url=accept_url,
            action_text='Accept Invitation',
            data={'invitation_token': invitation_token, 'hackathon_id': hackathon.id, 'action': 'judge_invitation'}
        )
    except User.DoesNotExist:
        # For non-existing users, use direct email
        from django.core.mail import EmailMessage
        from_email = settings.DEFAULT_EMAIL_HOST
        email = EmailMessage(subject, message, from_email=from_email, to=[email_address])
        email.send(fail_silently=True)