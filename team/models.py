from django.db import models
from django.conf import settings
from datetime import timedelta
from django.utils import timezone
import secrets

# Create your models here.

class Team(models.Model):
    name = models.CharField(max_length=50, null=False, blank=False)
    members = models.ManyToManyField('accounts.User', related_name='teams')
    organizer = models.ForeignKey('accounts.User', related_name='organized_teams', null=True, on_delete=models.SET_NULL)
    hackathon = models.ForeignKey('hackathon.Hackathon', related_name='teams', null=False, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('name', 'hackathon'), ('organizer', 'hackathon')]
        indexes = [
            models.Index(fields=['-created_at'], name='team_created_idx'),
            models.Index(fields=['hackathon', '-created_at'], name='team_hackathon_idx'),
            models.Index(fields=['organizer', '-created_at'], name='team_organizer_idx'),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.hackathon.title}"
    
    def get_projects(self):
        return self.projects.all()
    
    def get_submissions(self):
        return self.submissions.all()
    
    def get_prizes(self):
        # Return empty queryset since there's no Prize model related to Team
        from django.db.models import QuerySet
        return QuerySet().none()


class TeamInvitation(models.Model):
    team = models.ForeignKey(Team, related_name='invitations', on_delete=models.CASCADE)
    email = models.EmailField()
    invited_by = models.ForeignKey('accounts.User', related_name='sent_team_invitations', on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True)
    is_accepted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('team', 'email')]
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['email']),
        ]

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Invitation to {self.email} for {self.team.name}"

    def is_valid(self):
        """Check if invitation is still valid (not expired and not accepted)"""
        if self.is_accepted:
            return False
        
        # Invitations expire after 7 days
        expiry_time = self.created_at + timedelta(days=7)
        return timezone.now() < expiry_time
    
    def accept(self, user=None):
        """Accept the invitation and add user to team"""
        if not self.is_valid():
            raise ValueError("Invitation is expired or already accepted")
        
        from accounts.models import User
        
        # If no user provided, try to find user by email
        if not user:
            try:
                user = User.objects.get(email=self.email)
            except User.DoesNotExist:
                raise ValueError("User account required to accept invitation")
        
        # Verify user email matches invitation
        if user.email != self.email:
            raise ValueError("User email doesn't match invitation")
        
        # Add user to team
        self.team.members.add(user)
        
        # Mark invitation as accepted
        self.is_accepted = True
        self.accepted_at = timezone.now()
        self.save()
        
        # Update hackathon participant record if exists
        from hackathon.models import HackathonParticipant
        try:
            participant = HackathonParticipant.objects.get(
                hackathon=self.team.hackathon, 
                user=user
            )
            participant.team = self.team
            participant.looking_for_team = False
            participant.save()
        except HackathonParticipant.DoesNotExist:
            pass
        
        return self.team
    
class TeamJoinRequest(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="join_requests")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=[
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected')
        ],
        default='pending'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('team', 'user')