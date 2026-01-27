from rest_framework import status
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView
from django.core.mail import send_mail
from django.conf import settings
from .serializers import CreateTeamSerializer, TeamSerializer, UpdateTeamSerializer, AddMemberSerializer, RemoveMemberSerializer, LeaveTeamSerializer, AcceptTeamInvitationSerializer, TeamInvitationSerializer
from .models import Team
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import Team, TeamJoinRequest
from django.shortcuts import get_object_or_404

# Create your views here.

class TeamViewSet(ModelViewSet):
    queryset = Team.objects.all()
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'create':
            return CreateTeamSerializer
        elif self.action in ['update', 'partial_update']:
            return UpdateTeamSerializer
        return TeamSerializer
    
    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Team.objects.none()
        # Return teams where user is a member or organizer
        return Team.objects.filter(members=self.request.user).distinct()
    
    def perform_create(self, serializer):
        team = serializer.save()
        
        # Send email notifications to all team members
        send_mail(
            subject=f"Team Created for {team.hackathon.title}",
            message=f"Dear Team,\n\nA new team '{team.name}' has been created for '{team.hackathon.title}'.\nTeam Organizer: {((team.organizer.first_name + ' ' + team.organizer.last_name).strip() or team.organizer.username) if team.organizer else 'Unknown'}\nMembers: {', '.join([((member.first_name + ' ' + member.last_name).strip() or member.username) for member in team.members.all()])}\n\nGood luck with the hackathon!",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[member.email for member in team.members.all()],
            fail_silently=True
        )
    
    def perform_destroy(self, instance):
        if instance.organizer != self.request.user:
            raise PermissionDenied("You are not authorized to delete this team.")
        
        # Update participant records before deleting team
        from hackathon.models import HackathonParticipant
        for member in instance.members.all():
            try:
                participant = HackathonParticipant.objects.get(
                    hackathon=instance.hackathon, 
                    user=member
                )
                participant.team = None
                participant.looking_for_team = True
                participant.save()
            except HackathonParticipant.DoesNotExist:
                pass
        
        instance.delete()
    
    @swagger_auto_schema(
        request_body=AddMemberSerializer,
        responses={
            200: TeamSerializer,
            400: "Bad Request - validation errors",
            403: "Forbidden - not the team organizer", 
            404: "Team not found"
        },
        operation_description="Add a member to the team by email. Only team organizers can add members.",
        tags=['teams']
    )
    @action(detail=True, methods=['post'], serializer_class=AddMemberSerializer)
    def add_member(self, request, pk=None):
        """Send invitation to join the team"""
        team = self.get_object()
        serializer = AddMemberSerializer(team, data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        return Response({
            'message': result['message'],
            'user_exists': result['user_exists'],
            'invitation_id': result['invitation'].id
        }, status=status.HTTP_200_OK)
    
    @swagger_auto_schema(
        request_body=RemoveMemberSerializer,
        responses={
            200: TeamSerializer,
            400: "Bad Request - validation errors",
            403: "Forbidden - not the team organizer",
            404: "Team not found"
        },
        operation_description="Remove a member from the team by email. Only team organizers can remove members.",
        tags=['teams']
    )
    @action(detail=True, methods=['post'], serializer_class=RemoveMemberSerializer)
    def remove_member(self, request, pk=None):
        """Remove a member from the team"""
        team = self.get_object()
        serializer = RemoveMemberSerializer(team, data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'team': TeamSerializer(team).data}, status=status.HTTP_200_OK)
    
    @swagger_auto_schema(
        request_body=LeaveTeamSerializer,
        responses={
            200: "Successfully left the team",
            400: "Bad Request - validation errors",
            403: "Forbidden - not a team member or trying to leave as organizer",
            404: "Team not found"
        },
        operation_description="Leave a team. Team organizers cannot leave their own team.",
        tags=['teams']
    )
    @action(detail=True, methods=['post'], serializer_class=LeaveTeamSerializer)
    def leave_team(self, request, pk=None):
        """Leave a team"""
        team = self.get_object()
        serializer = LeaveTeamSerializer(team, data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        
        # Send notification email to team organizer and remaining members
        remaining_members = team.members.all()
        if remaining_members.exists():
            recipient_emails = [member.email for member in remaining_members]
            send_mail(
                subject=f"Member Left Team: {team.name}",
                message=f"Dear Team,\n\n{(request.user.first_name + ' ' + request.user.last_name).strip() or request.user.username} has left the team '{team.name}'.\n\nRemaining members: {', '.join([((member.first_name + ' ' + member.last_name).strip() or member.username) for member in remaining_members])}\n\nTeam Organizer: {((team.organizer.first_name + ' ' + team.organizer.last_name).strip() or team.organizer.username) if team.organizer else 'Unknown'}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=recipient_emails,
                fail_silently=True
            )
        
        return Response(
            {'message': f'You have successfully left the team "{team.name}".'},
            status=status.HTTP_200_OK
        )
    
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                'hackathon_id',
                openapi.IN_QUERY,
                description='The ID of the hackathon to get the user\'s team for',
                type=openapi.TYPE_INTEGER,
                required=True
            )
        ],
        responses={
            200: TeamSerializer,
            400: "Bad Request - hackathon_id parameter required",
            404: "Not Found - No team found for this hackathon"
        },
        operation_description="Get the team that the authenticated user is part of for a specific hackathon",
        tags=['teams']
    )
    @action(detail=False, methods=['get'])
    def by_hackathon(self, request):
        """Get user's team for a specific hackathon"""
        hackathon_id = request.query_params.get('hackathon_id')
        if not hackathon_id:
            return Response({'error': 'hackathon_id parameter required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            hackathon_id = int(hackathon_id)
        except ValueError:
            return Response({'error': 'Invalid hackathon_id'}, status=status.HTTP_400_BAD_REQUEST)
        
        team = Team.objects.filter(
            members=request.user, 
            hackathon_id=hackathon_id
        ).first()
        
        if team:
            return Response(TeamSerializer(team).data, status=status.HTTP_200_OK)
        return Response({'message': 'No team found for this hackathon'}, status=status.HTTP_404_NOT_FOUND)
    
    @swagger_auto_schema(
        request_body=AcceptTeamInvitationSerializer,
        responses={
            200: "Successfully joined team",
            400: "Bad Request - invalid or expired token",
            404: "Invitation not found"
        },
        operation_description="Accept a team invitation using the invitation token",
        tags=['teams']
    )
    @action(detail=False, methods=['post'])
    def accept_invitation(self, request):
        """Accept a team invitation"""
        serializer = AcceptTeamInvitationSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        return Response({
            'message': result['message'],
            'team': TeamSerializer(result['team']).data
        }, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        responses={
            200: TeamSerializer,
            404: "Team not found"
        },
        operation_description="Get team details by team ID",
        tags=['teams']
    )
    @action(detail=True, methods=['get'])
    def details(self, request, pk=None):
        """Get team details by ID"""
        try:
            team = Team.objects.get(pk=pk)
            return Response(TeamSerializer(team).data, status=status.HTTP_200_OK)
        except Team.DoesNotExist:
            return Response({'error': 'Team not found'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['post'])
    def request_to_join(self, request):
        if not request.user.is_authenticated:
            return Response({'error': 'Authentication required.'}, status=401)

        team_id = request.data.get('team_id')
        if not team_id:
            return Response({'error': 'team_id is required'}, status=400)

        team = get_object_or_404(Team, id=team_id)
        user = request.user

        if team.members.filter(id=user.id).exists():
            return Response({'error': 'You are already a member of this team'}, status=400)

        already_in_team = Team.objects.filter(hackathon=team.hackathon, members=user).exists()
        if already_in_team:
            return Response({'error': 'You are already in a team for this hackathon'}, status=400)

        join_request, created = TeamJoinRequest.objects.get_or_create(team=team, user=user)

        if not created:
            return Response({'error': 'Join request already sent'}, status=400)

        return Response({'message': 'Join request sent'}, status=201)


    @action(detail=False, methods=['post'])
    def approve_join_request(self, request):
        if not request.user.is_authenticated:
            return Response({'error': 'Authentication required.'}, status=401)

        team_id = request.data.get('team_id')
        user_id = request.data.get('user_id')  # optional: the user whose request is being approved

        if not team_id:
            return Response({'error': 'team_id is required'}, status=400)

        team = get_object_or_404(Team, id=team_id)

        if team.creator != request.user:
            return Response({'error': 'Only the team creator can approve requests.'}, status=403)

        # Get join request
        if user_id:
            join_request = TeamJoinRequest.objects.filter(team=team, user_id=user_id, status='pending').first()
        else:
            join_request = TeamJoinRequest.objects.filter(team=team, status='pending').first()

        if not join_request:
            return Response({'error': 'No pending join request found for this team.'}, status=404)

        join_request.status = 'approved'
        join_request.save()
        team.members.add(join_request.user)

        return Response({'message': f'{join_request.user.username} added to team.'}, status=200)
        
    @action(detail=False, methods=['post'])
    def reject_join_request(self, request):
        if not request.user.is_authenticated:
            return Response({'error': 'Authentication required.'}, status=401)

        team_id = request.data.get('team_id')
        if not team_id:
            return Response({'error': 'team_id is required'}, status=400)

        team = get_object_or_404(Team, id=team_id)

        if team.creator != request.user:
            return Response({'error': 'Only the team creator can reject requests.'}, status=403)

        join_request = TeamJoinRequest.objects.filter(team=team, status='pending').first()
        if not join_request:
            return Response({'error': 'No pending join request found for this team.'}, status=404)

        join_request.status = 'rejected'
        join_request.save()

        return Response({'message': f'{join_request.user.username} join request rejected.'}, status=200) 