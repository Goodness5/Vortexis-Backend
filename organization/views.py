from rest_framework import status
from rest_framework.response import Response
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.pagination import PageNumberPagination
from django.db.models import Prefetch
from accounts.permissions import IsOrganizer, IsAdmin, IsOrganizationOrganizer
from .serializers import (
    OrganizationSerializer, CreateOrganizationSerializer,
    UpdateOrganizationSerializer, AddModeratorSerializer,
    RemoveModeratorSerializer, ApproveOrganizationSerializer,
    ModeratorInvitationSerializer, CreateModeratorInvitationSerializer,
    AcceptInvitationSerializer, DeclineInvitationSerializer
)
from .models import Organization, ModeratorInvitation
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi


class OrganizationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

class CreateOrganizationView(GenericAPIView):
    serializer_class = CreateOrganizationSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    @swagger_auto_schema(
        request_body=CreateOrganizationSerializer,
        manual_parameters=[
            openapi.Parameter(
                'logo_file',
                openapi.IN_FORM,
                description="Organization logo image file (JPEG or PNG, less than 2 MB, recommended 480x480px)",
                type=openapi.TYPE_FILE,
                required=False
            ),
        ],
        responses={201: OrganizationSerializer, 400: 'Bad Request'},
        operation_description="Create a new organization.",
        tags=['organization']
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        organization = serializer.save()
        return Response(OrganizationSerializer(organization).data, status=status.HTTP_201_CREATED)

class UpdateOrganizationView(GenericAPIView):
    serializer_class = UpdateOrganizationSerializer
    permission_classes = [IsAuthenticated, IsOrganizationOrganizer]
    parser_classes = (MultiPartParser, FormParser)

    @swagger_auto_schema(
        request_body=UpdateOrganizationSerializer,
        manual_parameters=[
            openapi.Parameter(
                'logo_file',
                openapi.IN_FORM,
                description="Organization logo image file (JPEG or PNG, less than 2 MB, recommended 480x480px)",
                type=openapi.TYPE_FILE,
                required=False
            ),
        ],
        responses={200: OrganizationSerializer, 404: 'Not Found'},
        operation_description="Update an organization.",
        tags=['organization']
    )
    def put(self, request, organization_id):
        try:
            organization = Organization.objects.get(id=organization_id)
        except Organization.DoesNotExist:
            return Response({'error': 'Organization not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(organization, data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        organization = serializer.save()
        return Response(OrganizationSerializer(organization).data)

class DeleteOrganizationView(GenericAPIView):
    permission_classes = [IsAuthenticated, IsOrganizationOrganizer]

    @swagger_auto_schema(
        responses={204: 'No Content', 403: 'Forbidden', 404: 'Not Found'},
        operation_description="Delete an organization.",
        tags=['organization']
    )
    def delete(self, request, organization_id):
        try:
            organization = Organization.objects.get(id=organization_id)
        except Organization.DoesNotExist:
            return Response({'error': 'Organization not found.'}, status=status.HTTP_404_NOT_FOUND)
        if organization.organizer != request.user:
            return Response({'error': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)
        organization.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
class DeleteMyOrganizationView(GenericAPIView):
    permission_classes = [IsAuthenticated, IsOrganizationOrganizer]

    @swagger_auto_schema(
        responses={204: 'No Content', 403: 'Forbidden', 404: 'Not Found'},
        operation_description="Delete an organization owned by the authenticated user.",
        tags=['organization']
    )
    def delete(self, request, organization_id):
        try:
            organization = Organization.objects.get(
                id=organization_id,
                organizer=request.user
            )
        except Organization.DoesNotExist:
            return Response(
                {'error': 'Organization not found or not owned by you.'},
                status=status.HTTP_404_NOT_FOUND
            )

        organization.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

class GetOrganizationView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: OrganizationSerializer, 404: 'Not Found'},
        operation_description="Retrieve an organization by ID.",
        tags=['organization']
    )
    def get(self, request, organization_id):
        try:
            organization = Organization.objects.select_related(
                'organizer'
            ).prefetch_related(
                'moderators'
            ).get(id=organization_id)
        except Organization.DoesNotExist:
            return Response({'error': 'Organization not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(OrganizationSerializer(organization).data)

class GetOrganizationsView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = OrganizationPagination

    @swagger_auto_schema(
        responses={200: OrganizationSerializer(many=True)},
        operation_description="Retrieve all organizations. Ordered by latest first.",
        tags=['organization']
    )
    def get(self, request):
        queryset = Organization.objects.select_related(
            'organizer'
        ).prefetch_related(
            'moderators'
        ).order_by('-created_at')
        
        # Filter by approval status if requested
        is_approved = request.query_params.get('is_approved')
        if is_approved is not None:
            queryset = queryset.filter(is_approved=is_approved.lower() == 'true')
        
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        
        if page is not None:
            serializer = OrganizationSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        
        serializer = OrganizationSerializer(queryset, many=True)
        return Response(serializer.data)


class GetUserOrganizationsView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = OrganizationPagination

    @swagger_auto_schema(
        responses={200: OrganizationSerializer(many=True)},
        operation_description="Retrieve all organizations owned by the authenticated user. Ordered by latest first.",
        tags=['organization']
    )
    def get(self, request):
        queryset = Organization.objects.filter(
            organizer=request.user
        ).select_related(
            'organizer'
        ).prefetch_related(
            'moderators'
        ).order_by('-created_at')
        
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        
        if page is not None:
            serializer = OrganizationSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        
        serializer = OrganizationSerializer(queryset, many=True)
        return Response(serializer.data)

class GetUnapprovedOrganizationsView(GenericAPIView):
    permission_classes = [IsAuthenticated, IsAdmin]
    pagination_class = OrganizationPagination

    @swagger_auto_schema(
        responses={200: OrganizationSerializer(many=True)},
        operation_description="Retrieve all unapproved organizations. Ordered by latest first.",
        tags=['organization']
    )
    def get(self, request):
        queryset = Organization.objects.filter(
            is_approved=False
        ).select_related(
            'organizer'
        ).prefetch_related(
            'moderators'
        ).order_by('-created_at')
        
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        
        if page is not None:
            serializer = OrganizationSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
        
        serializer = OrganizationSerializer(queryset, many=True)
        return Response(serializer.data)

class ApproveOrganizationView(GenericAPIView):
    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = ApproveOrganizationSerializer

    @swagger_auto_schema(
        responses={200: OrganizationSerializer, 404: 'Not Found'},
        operation_description="Approve an organization.",
        tags=['organization']
    )
    def post(self, request, organization_id):
        try:
            organization = Organization.objects.get(id=organization_id)
        except Organization.DoesNotExist:
            return Response({'error': 'Organization not found.'}, status=status.HTTP_404_NOT_FOUND)
        organization.is_approved = True
        organization.save()
        organizer = organization.organizer
        if organizer:
            organizer.is_organizer = True
            organizer.save()
            # Send approval email
            from django.core.mail import send_mail
            send_mail(
                subject='Organization Approved',
                message=f'Your organization "{organization.name}" has been approved.',
                from_email='noreply@hackathon.com',
                recipient_list=[organizer.email],
                fail_silently=True
            )
        return Response(OrganizationSerializer(organization).data)

class AddModeratorView(GenericAPIView):
    serializer_class = AddModeratorSerializer
    permission_classes = [IsAuthenticated, IsOrganizationOrganizer]

    @swagger_auto_schema(
        request_body=serializer_class,
        responses={200: OrganizationSerializer, 404: 'Not Found'},
        operation_description="Add moderators to an organization.",
        tags=['organization']
    )
    def post(self, request, organization_id):
        try:
            organization = Organization.objects.get(id=organization_id)
        except Organization.DoesNotExist:
            return Response({'error': 'Organization not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(organization, data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        organization = serializer.save()
        return Response(OrganizationSerializer(organization).data)

class RemoveModeratorView(GenericAPIView):
    serializer_class = RemoveModeratorSerializer
    permission_classes = [IsAuthenticated, IsOrganizationOrganizer]

    @swagger_auto_schema(
        request_body=serializer_class,
        responses={200: OrganizationSerializer, 404: 'Not Found'},
        operation_description="Remove moderators from an organization.",
        tags=['organization']
    )
    def post(self, request, organization_id):
        try:
            organization = Organization.objects.get(id=organization_id)
        except Organization.DoesNotExist:
            return Response({'error': 'Organization not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(organization, data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        organization = serializer.save()
        return Response(OrganizationSerializer(organization).data)