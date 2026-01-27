from urllib import request
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from django.utils import timezone
from notifications.services import NotificationService

from accounts.models import User
from .models import Team, TeamInvitation


class CreateTeamSerializer(serializers.ModelSerializer):
    hackathon_id = serializers.IntegerField(write_only=True)
    members = serializers.ListField(
        child=serializers.EmailField(),
        write_only=True,
        required=False,
        allow_empty=True,
        help_text="List of user emails to invite as team members. Can be empty - team creator is automatically added."
    )
    
    class Meta:
        model = Team
        fields = ['name', 'members', 'hackathon_id']
    
    def validate(self, data):
        from hackathon.models import Hackathon, HackathonParticipant
        
        request = self.context.get('request')
        if not request:
            raise serializers.ValidationError("Request context is required.")
        
        user = request.user
        hackathon_id = data.get('hackathon_id')
        
        if not data.get('name'):
            raise serializers.ValidationError("Team name is required.")
        
        # Validate hackathon exists
        try:
            hackathon = Hackathon.objects.get(id=hackathon_id)
        except Hackathon.DoesNotExist:
            raise serializers.ValidationError("Hackathon does not exist.")
        
        # Check if organizer already has a team for this hackathon
        if Team.objects.filter(hackathon=hackathon, organizer=user).exists():
            raise serializers.ValidationError("You already have a team for this hackathon.")
        
        # Check if team creator is registered for the hackathon
        if not HackathonParticipant.objects.filter(hackathon=hackathon, user=user).exists():
            raise serializers.ValidationError("You must be registered for this hackathon to create a team.")
            
        member_emails = data.get('members', [])

        # Remove creator's email from members list if they included themselves
        if user.email in member_emails:
            member_emails.remove(user.email)

        if len(member_emails) != len(set(member_emails)):
            raise serializers.ValidationError("Duplicate member emails are not allowed.")

        # All member emails will receive invitations (no auto-adding)
        invitation_emails = member_emails.copy()

        # For existing users, validate they can join teams but don't auto-add them
        for email in member_emails:
            try:
                member = User.objects.get(email=email)
                # User exists - check if they're registered for hackathon and available
                if not HackathonParticipant.objects.filter(hackathon=hackathon, user=member).exists():
                    raise serializers.ValidationError(f"User with email {email} is not registered for this hackathon.")
                # Check if member already has a team for this hackathon
                participant = HackathonParticipant.objects.get(hackathon=hackathon, user=member)
                if participant.has_team:
                    raise serializers.ValidationError(f"User with email {email} is already part of a team for this hackathon.")
            except User.DoesNotExist:
                # User doesn't exist - that's fine, they'll get an invitation to sign up
                pass

        # Check team size constraints (including the creator)
        team_size = len(member_emails) + 1  # +1 for the creator
        if team_size < hackathon.min_team_size or team_size > hackathon.max_team_size:
            raise serializers.ValidationError(f"Team size must be between {hackathon.min_team_size} and {hackathon.max_team_size} members.")
        
        # Add validated data for use in create method
        data['invitation_emails'] = invitation_emails
        data['hackathon'] = hackathon
        
        return data
    
    def create(self, validated_data):
        from hackathon.models import HackathonParticipant
        from django.core.mail import send_mail
        from django.conf import settings

        request = self.context.get('request')
        user = request.user

        hackathon = validated_data.pop('hackathon')
        invitation_emails = validated_data.pop('invitation_emails')
        validated_data.pop('hackathon_id')
        validated_data.pop('members')

        # Create team
        team = Team.objects.create(
            name=validated_data['name'],
            organizer=user,
            hackathon=hackathon
        )

        # Add only the creator as initial member
        team.members.set([user])

        # Update creator's participant record
        participant = HackathonParticipant.objects.get(hackathon=hackathon, user=user)
        participant.team = team
        participant.looking_for_team = False
        participant.save()

        # Send invitations to ALL invited members
        for email in invitation_emails:
            invitation = TeamInvitation.objects.create(
                team=team,
                email=email,
                invited_by=user
            )

            # Check if user already exists
            try:
                existing_user = User.objects.get(email=email)
                user_exists = True
            except User.DoesNotExist:
                existing_user = None
                user_exists = False

            # Send invitation email
            organizer_name = (user.first_name + ' ' + user.last_name).strip() or user.username

            if user_exists:
                # User exists - direct invitation
                subject = f"Team Invitation: Join {team.name} for {hackathon.title}"
                message = f"""
Hi there!

{organizer_name} has invited you to join the team "{team.name}" for the hackathon "{hackathon.title}".

Click the link below to accept this invitation:
{settings.FRONTEND_URL}/team-invitation/{invitation.token}

This invitation will expire in 7 days.

Good luck with the hackathon!

Best regards,
The Vortexis Team
"""
            else:
                # User doesn't exist - signup invitation
                subject = f"Join {team.name} for {hackathon.title} - Create Account"
                message = f"""
Hi there!

{organizer_name} has invited you to join the team "{team.name}" for the hackathon "{hackathon.title}".

To accept this invitation, you'll need to:
1. Create an account: {settings.FRONTEND_URL}/signup?invitation={invitation.token}
2. Register for the hackathon
3. Accept the team invitation

After completing these steps, you'll be added to the team.

This invitation will expire in 7 days.

Good luck with the hackathon!

Best regards,
The Vortexis Team
"""

            if user_exists:
                # User exists - use NotificationService
                action_url = f"{settings.FRONTEND_URL}/team-invitation/{invitation.token}"
                NotificationService.send_notification(
                    user=existing_user,
                    title=subject,
                    message=message.strip(),
                    category='account',
                    priority='normal',
                    data={
                        'team_id': team.id,
                        'hackathon_id': hackathon.id,
                        'invitation_token': invitation.token,
                        'team_name': team.name,
                        'hackathon_title': hackathon.title,
                        'organizer_name': organizer_name
                    },
                    action_url=action_url,
                    action_text='Accept Invitation',
                    send_email=True,
                    send_in_app=True
                )
            else:
                # User doesn't exist - fallback to direct email
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=True
                )

        return team


class TeamSerializer(serializers.ModelSerializer):
    organizer = serializers.SerializerMethodField()
    creator = serializers.SerializerMethodField()  # Alias for organizer for clarity
    members = serializers.SerializerMethodField()
    hackathon = serializers.SerializerMethodField()
    projects = serializers.SerializerMethodField()
    submissions = serializers.SerializerMethodField()
    is_member_of = serializers.SerializerMethodField()   

    class Meta:
        model = Team
        fields = ['id', 'name', 'organizer', 'creator', 'members', 'hackathon', 'projects', 'submissions', 'is_member_of', 'created_at', 'updated_at']

    def get_organizer(self, obj):
        if obj.organizer:
            organizer_data = {
                'id': obj.organizer.id,
                'username': obj.organizer.username,
                'first_name': obj.organizer.first_name,
                'last_name': obj.organizer.last_name
            }

            # Add profile picture if available
            if hasattr(obj.organizer, 'profile') and obj.organizer.profile.profile_picture:
                organizer_data['profile_picture'] = obj.organizer.profile.profile_picture

            return organizer_data
        return None

    def get_creator(self, obj):
        # Alias for organizer to make it clear who created the team
        return self.get_organizer(obj)

    def get_members(self, obj):
        members_data = []
        for member in obj.members.all():
            member_data = {
                'id': member.id,
                'username': member.username,
                'first_name': member.first_name,
                'last_name': member.last_name,
                'is_creator': member == obj.organizer  # Flag to identify creator among members
            }

            # Add profile picture if available
            if hasattr(member, 'profile') and member.profile.profile_picture:
                member_data['profile_picture'] = member.profile.profile_picture

            members_data.append(member_data)

        return members_data
    
    def get_hackathon(self, obj):
        return {
            'id': obj.hackathon.id, 
            'title': obj.hackathon.title,
            'start_date': obj.hackathon.start_date,
            'end_date': obj.hackathon.end_date
        }

    def get_projects(self, obj):
        return [{'id': project.id, 'title': project.title} for project in obj.get_projects()]
    
    def get_submissions(self, obj):
        return [{'id': submission.id, 'project_title': submission.project.title if submission.project else None} for submission in obj.get_submissions()]
    def get_is_member_of(self, obj):
      request = self.context.get('request')

      if not request or not request.user.is_authenticated:
         return False

      return obj.members.filter(id=request.user.id).exists()

class UpdateTeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ['name']

    def validate(self, data):
        request = self.context.get('request')
        if not request:
            raise serializers.ValidationError("Request context is required.")
        user = request.user
        team = self.instance
        if team.organizer != user:
            raise AuthenticationFailed("You are not authorized to update this team.")
        
        if not data.get('name'):
            raise serializers.ValidationError("Team name is required.")
        
        # Check if name is unique within the same hackathon
        if Team.objects.filter(
            name=data['name'], 
            hackathon=team.hackathon
        ).exclude(id=team.id).exists():
            raise serializers.ValidationError("A team with this name already exists in this hackathon.")
        
        return data
    
    def update(self, instance, validated_data):
        instance.name = validated_data['name']
        instance.save()
        return instance


class AddMemberSerializer(serializers.Serializer):
    member_email = serializers.EmailField()

    def validate_member_email(self, value):
        from hackathon.models import HackathonParticipant
        from .models import TeamInvitation
        
        request = self.context.get('request')
        if not request:
            raise serializers.ValidationError("Request context is required.")
        user = request.user
        team = self.instance
        if team and team.organizer != user:
            raise AuthenticationFailed("You are not authorized to add members to this team.")
        
        # Check if there's already an invitation for this email
        existing_invitation = TeamInvitation.objects.filter(
            team=team, 
            email=value, 
            is_accepted=False
        ).first()
        
        if existing_invitation and existing_invitation.is_valid():
            raise serializers.ValidationError("An invitation has already been sent to this email.")
        
        # Check if user exists and is already a member
        try:
            member = User.objects.get(email=value)
            if member in team.members.all():
                raise serializers.ValidationError("User is already a member of this team.")
            
            # If user exists and is registered for hackathon, check if they have a team
            if HackathonParticipant.objects.filter(hackathon=team.hackathon, user=member).exists():
                participant = HackathonParticipant.objects.get(hackathon=team.hackathon, user=member)
                if participant.has_team:
                    raise serializers.ValidationError("User is already part of a team for this hackathon.")
        except User.DoesNotExist:
            # User doesn't exist yet - this is fine, we'll send them an invitation
            pass
        
        # Check team size constraints (including pending invitations)
        pending_invitations = TeamInvitation.objects.filter(
            team=team, 
            is_accepted=False
        ).count()
        current_size = team.members.count() + pending_invitations
        
        if current_size >= team.hackathon.max_team_size:
            raise serializers.ValidationError("Team has reached maximum size including pending invitations.")
        
        return value
    
    def save(self):
        from django.core.mail import send_mail
        from django.conf import settings
        from .models import TeamInvitation
        
        email = self.validated_data['member_email']
        team = self.instance
        request = self.context.get('request')
        
        # Check if user already exists
        try:
            existing_user = User.objects.get(email=email)
            user_exists = True
        except User.DoesNotExist:
            existing_user = None
            user_exists = False
        
        # Create or update invitation
        invitation, created = TeamInvitation.objects.get_or_create(
            team=team,
            email=email,
            defaults={
                'invited_by': request.user,
                'is_accepted': False
            }
        )
        
        # If invitation already existed but was expired, create a new token
        if not created and not invitation.is_valid():
            import secrets
            invitation.token = secrets.token_urlsafe(32)
            invitation.created_at = timezone.now()
            invitation.is_accepted = False
            invitation.save()
        
        # Prepare email content
        hackathon_title = team.hackathon.title
        team_name = team.name
        organizer_name = (request.user.first_name + ' ' + request.user.last_name).strip() or request.user.username
        
        if user_exists:
            # User exists - direct invitation
            subject = f"Team Invitation: Join {team_name} for {hackathon_title}"
            message = f"""
            Hi there!
            
            {organizer_name} has invited you to join the team "{team_name}" for the hackathon "{hackathon_title}".
            
            Click the link below to accept this invitation:
            {settings.FRONTEND_URL}/team-invitation/{invitation.token}
            
            This invitation will expire in 7 days.
            
            Good luck with the hackathon!
            
            Best regards,
            The Vortexis Team
            """
        else:
            # User doesn't exist - signup invitation
            subject = f"Join {team_name} for {hackathon_title} - Create Account"
            message = f"""
            Hi there!
            
            {organizer_name} has invited you to join the team "{team_name}" for the hackathon "{hackathon_title}".
            
            To accept this invitation, you'll need to create an account first:
            {settings.FRONTEND_URL}/signup?invitation={invitation.token}
            
            After creating your account, you'll automatically be added to the team.
            
            This invitation will expire in 7 days.
            
            Good luck with the hackathon!
            
            Best regards,
            The Vortexis Team
            """
        
        # Send email notification
        if user_exists:
            # User exists - use NotificationService
            action_url = f"{settings.FRONTEND_URL}/team-invitation/{invitation.token}"
            NotificationService.send_notification(
                user=existing_user,
                title=subject,
                message=message.strip(),
                category='account',
                priority='normal',
                data={
                    'team_id': team.id,
                    'hackathon_id': team.hackathon.id,
                    'invitation_token': invitation.token,
                    'team_name': team_name,
                    'hackathon_title': hackathon_title,
                    'organizer_name': organizer_name
                },
                action_url=action_url,
                action_text='Accept Invitation',
                send_email=True,
                send_in_app=True
            )
        else:
            # User doesn't exist - fallback to direct email
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False
            )
        
        return {
            'invitation': invitation,
            'user_exists': user_exists,
            'message': f'Invitation sent to {email}'
        }


class RemoveMemberSerializer(serializers.Serializer):
    member_email = serializers.EmailField()

    def validate_member_email(self, value):
        request = self.context.get('request')
        if not request:
            raise serializers.ValidationError("Request context is required.")
        user = request.user
        team = self.instance
        if team.organizer != user:
            raise AuthenticationFailed("You are not authorized to remove members from this team.")
        
        try:
            member = User.objects.get(email=value)
        except User.DoesNotExist:
            raise serializers.ValidationError("User with this email does not exist.")
        
        if member not in team.members.all():
            raise serializers.ValidationError("User is not a member of this team.")
        
        if member == team.organizer:
            raise serializers.ValidationError("Cannot remove the team organizer.")
        
        # Check team size constraints
        if team.members.count() <= team.hackathon.min_team_size:
            raise serializers.ValidationError("Cannot remove member. Team would fall below minimum size.")
        
        return value
    
    def save(self):
        from hackathon.models import HackathonParticipant
        
        member = User.objects.get(email=self.validated_data['member_email'])
        team = self.instance
        
        # Remove member from team
        team.members.remove(member)
        
        # Update participant record
        try:
            participant = HackathonParticipant.objects.get(hackathon=team.hackathon, user=member)
            participant.team = None
            participant.looking_for_team = True
            participant.save()
        except HackathonParticipant.DoesNotExist:
            pass
        
        return team


class LeaveTeamSerializer(serializers.Serializer):
    """Serializer for users to leave a team"""
    
    def validate(self, data):
        request = self.context.get('request')
        if not request:
            raise serializers.ValidationError("Request context is required.")
        
        user = request.user
        team = self.instance
        
        if user not in team.members.all():
            raise serializers.ValidationError("You are not a member of this team.")
        
        if user == team.organizer:
            raise serializers.ValidationError("Team organizers cannot leave their own team. Delete the team instead.")
        
        # Check team size constraints
        if team.members.count() <= team.hackathon.min_team_size:
            raise serializers.ValidationError("Cannot leave team. Team would fall below minimum size.")
        
        return data
    
    def save(self):
        from hackathon.models import HackathonParticipant
        
        user = self.context.get('request').user
        team = self.instance
        
        # Remove user from team
        team.members.remove(user)
        
        # Update hackathon participant record
        try:
            participant = HackathonParticipant.objects.get(hackathon=team.hackathon, user=user)
            participant.team = None
            participant.looking_for_team = True
            participant.save()
        except HackathonParticipant.DoesNotExist:
            pass
        
        return team


class AcceptTeamInvitationSerializer(serializers.Serializer):
    token = serializers.CharField()

    def validate_token(self, value):
        from .models import TeamInvitation
        
        try:
            invitation = TeamInvitation.objects.get(token=value)
            if not invitation.is_valid():
                raise serializers.ValidationError("Invitation token is invalid or expired.")
            return invitation
        except TeamInvitation.DoesNotExist:
            raise serializers.ValidationError("Invalid invitation token.")

    def save(self):
        invitation = self.validated_data['token']
        request = self.context.get('request')
        user = request.user
        
        # Accept the invitation
        team = invitation.accept(user)
        
        return {
            'team': team,
            'message': f'Successfully joined team "{team.name}" for hackathon "{team.hackathon.title}"'
        }


class TeamInvitationSerializer(serializers.ModelSerializer):
    team = serializers.SerializerMethodField()
    hackathon = serializers.SerializerMethodField()
    invited_by = serializers.SerializerMethodField()

    class Meta:
        model = TeamInvitation
        fields = ['id', 'email', 'team', 'hackathon', 'invited_by', 'is_accepted', 'created_at']
        read_only_fields = ['id', 'email', 'team', 'hackathon', 'invited_by', 'is_accepted', 'created_at']

    def get_team(self, obj):
        return {
            'id': obj.team.id,
            'name': obj.team.name
        }

    def get_hackathon(self, obj):
        return {
            'id': obj.team.hackathon.id,
            'title': obj.team.hackathon.title,
            'start_date': obj.team.hackathon.start_date,
            'end_date': obj.team.hackathon.end_date
        }

    def get_invited_by(self, obj):
        return {
            'id': obj.invited_by.id,
            'username': obj.invited_by.username,
            'first_name': obj.invited_by.first_name,
            'last_name': obj.invited_by.last_name
        }

