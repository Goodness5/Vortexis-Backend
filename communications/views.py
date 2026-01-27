from django.db import transaction, models
from django.db.models import Q, Prefetch
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied, NotFound
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .models import Conversation, ConversationParticipant, Message
from .serializers import (
    ConversationSerializer,
    ConversationParticipantSerializer,
    MessageSerializer,
    CreateDMSerializer,
    CreateTeamConversationSerializer,
    CreateJudgesConversationSerializer,
)


class ConversationViewSet(viewsets.ModelViewSet):
    queryset = Conversation.objects.all()
    serializer_class = ConversationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Conversation.objects.none()

        user = self.request.user
        if not user.is_authenticated:
            return Conversation.objects.none()

        # Optimize queries with select_related and prefetch_related
        return Conversation.objects.filter(
            participants__user=user
        ).select_related(
            'team', 'hackathon', 'organization', 'created_by'
        ).prefetch_related(
            'participants__user',
            Prefetch(
                'messages',
                queryset=Message.objects.select_related('sender').order_by('-created_at')[:1],
                to_attr='_prefetched_last_message_list'
            )
        ).distinct().order_by('-updated_at')  # Order by most recently updated

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @swagger_auto_schema(
        request_body=CreateDMSerializer,
        responses={
            200: ConversationSerializer,
            201: ConversationSerializer,
            400: "Bad Request - validation errors"
        },
        operation_description="Create or retrieve a direct message conversation with another user",
        tags=['communications']
    )
    @action(detail=False, methods=['post'], url_path='dm')
    def create_dm(self, request):
        serializer = CreateDMSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_user_id = serializer.validated_data['user_id']
        user = request.user

        if target_user_id == user.id:
            raise ValidationError("Cannot create DM with yourself.")

        # Check if a DM already exists between the two users
        existing = Conversation.objects.filter(
            type='dm',
            participants__user_id__in=[user.id, target_user_id]
        ).annotate(num_participants=models.Count('participants')).filter(num_participants=2).first()

        if existing:
            return Response(ConversationSerializer(existing).data, status=status.HTTP_200_OK)

        with transaction.atomic():
            conv = Conversation.objects.create(type='dm', created_by=user)
            ConversationParticipant.objects.bulk_create([
                ConversationParticipant(conversation=conv, user=user, is_admin=True),
                ConversationParticipant(conversation=conv, user_id=target_user_id, is_admin=False),
            ])
        return Response(ConversationSerializer(conv).data, status=status.HTTP_201_CREATED)

    @swagger_auto_schema(
        request_body=CreateTeamConversationSerializer,
        responses={
            200: ConversationSerializer,
            201: ConversationSerializer,
            400: "Bad Request - validation errors",
            403: "Forbidden - not authorized for this team",
            404: "Team not found"
        },
        operation_description="Create or retrieve a team conversation",
        tags=['communications']
    )
    @action(detail=False, methods=['post'], url_path='team')
    def create_team_conversation(self, request):
        from team.models import Team
        serializer = CreateTeamConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        team_id = serializer.validated_data['team_id']
        user = request.user

        try:
            team = Team.objects.select_related('hackathon').prefetch_related('members').get(id=team_id)
        except Team.DoesNotExist:
            raise NotFound("Team not found")

        if not team.members.filter(id=user.id).exists() and team.organizer_id != user.id:
            raise PermissionDenied("Not authorized for this team.")

        conv, created = Conversation.objects.get_or_create(type='team', team=team, defaults={
            'created_by': user,
            'hackathon': team.hackathon,
            'title': f"Team: {team.name}",
        })

        # Always sync participants to ensure new team members are included
        ConversationParticipant.objects.bulk_create([
            ConversationParticipant(conversation=conv, user=member, is_admin=(member.id == team.organizer_id))
            for member in team.members.all()
        ], ignore_conflicts=True)

        # Ensure team organizer is always a participant
        if team.organizer_id and not team.members.filter(id=team.organizer_id).exists():
            ConversationParticipant.objects.get_or_create(
                conversation=conv,
                user_id=team.organizer_id,
                defaults={'is_admin': True}
            )

        return Response(ConversationSerializer(conv).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @swagger_auto_schema(
        request_body=CreateJudgesConversationSerializer,
        responses={
            200: ConversationSerializer,
            201: ConversationSerializer,
            400: "Bad Request - validation errors",
            403: "Forbidden - not authorized to create judges conversation",
            404: "Hackathon not found"
        },
        operation_description="Create or retrieve a judges conversation for a hackathon",
        tags=['communications']
    )
    @action(detail=False, methods=['post'], url_path='judges')
    def create_judges_conversation(self, request):
        from hackathon.models import Hackathon
        from organization.models import Organization
        serializer = CreateJudgesConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        hackathon_id = serializer.validated_data['hackathon_id']
        include_organizers = serializer.validated_data['include_organizers']
        include_org_members = serializer.validated_data['include_org_members']

        user = request.user

        try:
            hackathon = Hackathon.objects.select_related('organization').prefetch_related('judges', 'organization__moderators').get(id=hackathon_id)
        except Hackathon.DoesNotExist:
            raise NotFound("Hackathon not found")

        # Authorization: judges or organizers of this hackathon/org
        is_judge = hackathon.judges.filter(id=user.id).exists()
        is_organizer = False
        if hackathon.organization and (hackathon.organization.organizer_id == user.id or hackathon.organization.moderators.filter(id=user.id).exists()):
            is_organizer = True

        if not (is_judge or is_organizer):
            raise PermissionDenied("Not authorized to create judges conversation.")

        conv, created = Conversation.objects.get_or_create(
            type='judges', hackathon=hackathon,
            defaults={
                'created_by': user,
                'organization': hackathon.organization,
                'title': f"Judges: {hackathon.title}"
            }
        )

        # Always sync participants, not just on creation
        # This ensures newly added judges are included in existing conversations
        participants = set(hackathon.judges.values_list('id', flat=True))
        if include_organizers and hackathon.organization and hackathon.organization.organizer_id:
            participants.add(hackathon.organization.organizer_id)
        if include_org_members and hackathon.organization:
            participants.update(hackathon.organization.moderators.values_list('id', flat=True))

        # Add any missing participants
        ConversationParticipant.objects.bulk_create([
            ConversationParticipant(
                conversation=conv,
                user_id=uid,
                is_admin=True if (hackathon.organization and uid == hackathon.organization.organizer_id) else False
            )
            for uid in participants
        ], ignore_conflicts=True)

        return Response(ConversationSerializer(conv).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class MessageViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]

    @property
    def paginator(self):
        from rest_framework.pagination import PageNumberPagination

        class MessagePagination(PageNumberPagination):
            page_size = 50
            page_size_query_param = 'page_size'
            max_page_size = 100

        if not hasattr(self, '_paginator'):
            self._paginator = MessagePagination()
        return self._paginator

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Message.objects.none()

        user = self.request.user
        if not user.is_authenticated:
            return Message.objects.none()

        conversation_id = self.kwargs.get('conversation_pk')

        # Ensure user is participant
        if not ConversationParticipant.objects.filter(conversation_id=conversation_id, user=user).exists():
            return Message.objects.none()

        # Optimize query with select_related
        # Include deleted messages for the owner to allow viewing their own deleted messages
        queryset = Message.objects.select_related('sender', 'conversation').filter(
            conversation_id=conversation_id
        )
        
        # Filter out deleted messages unless user is the sender
        if self.action == 'list':
            queryset = queryset.filter(
                Q(is_deleted=False) | Q(sender=user, is_deleted=True)
            )
        
        return queryset.order_by('created_at')

    def get_object(self):
        obj = super().get_object()
        # Ensure user is participant in the conversation
        conversation_id = self.kwargs.get('conversation_pk')
        user = self.request.user
        if not user.is_authenticated:
            raise PermissionDenied("Authentication required.")
        if not ConversationParticipant.objects.filter(conversation_id=conversation_id, user=user).exists():
            raise PermissionDenied("You are not a participant in this conversation.")
        return obj

    def perform_create(self, serializer):
        conversation_id = self.kwargs.get('conversation_pk')
        user = self.request.user

        if not user.is_authenticated:
            raise PermissionDenied("Authentication required.")

        # Check if conversation exists
        try:
            conversation = Conversation.objects.get(id=conversation_id)
        except Conversation.DoesNotExist:
            raise NotFound("Conversation not found")

        # Check if user can post
        participant = ConversationParticipant.objects.filter(
            conversation_id=conversation_id,
            user=user
        ).first()

        if not participant:
            raise PermissionDenied("You are not a participant in this conversation.")

        if not participant.can_post:
            raise PermissionDenied("You are not allowed to post in this conversation.")

        serializer.save(sender=user, conversation_id=conversation_id)

    def perform_update(self, serializer):
        message = self.get_object()
        user = self.request.user
        
        # Only the sender can edit their message
        if message.sender != user:
            raise PermissionDenied("You can only edit your own messages.")
        
        # Check if message is deleted
        if message.is_deleted:
            raise ValidationError("Cannot edit a deleted message.")
        
        # Use the model's edit method
        new_content = serializer.validated_data.get('content')
        if new_content:
            message.edit(new_content)
            # Update the serializer instance
            serializer.instance = message

    @swagger_auto_schema(
        responses={
            204: "Message deleted successfully",
            403: "Forbidden - not the message sender",
            404: "Message not found"
        },
        operation_description="Delete a message (soft delete). Only the sender can delete their own message.",
        tags=['communications']
    )
    @action(detail=True, methods=['delete'])
    def delete_message(self, request, conversation_pk=None, pk=None):
        message = self.get_object()
        user = request.user
        
        # Only the sender can delete their message
        if message.sender != user:
            raise PermissionDenied("You can only delete your own messages.")
        
        # Soft delete the message
        message.soft_delete()
        
        return Response(status=status.HTTP_204_NO_CONTENT)

