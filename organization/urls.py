from django.urls import path
from .views import (
    CreateOrganizationView, UpdateOrganizationView, DeleteOrganizationView,
    GetOrganizationView, GetOrganizationsView, GetUserOrganizationsView,
    GetUnapprovedOrganizationsView, ApproveOrganizationView, AddModeratorView,
    RemoveModeratorView, DeleteMyOrganizationView
)
from .invitation_views import (
    CreateModeratorInvitationView, GetInvitationView, AcceptInvitationView,
    DeclineInvitationView, GetSentInvitationsView, GetReceivedInvitationsView
)

urlpatterns = [
    path('create/', CreateOrganizationView.as_view(), name='create_organization'),
    path('update/<int:organization_id>/', UpdateOrganizationView.as_view(), name='update_organization'),
    path('delete/<int:organization_id>/', DeleteOrganizationView.as_view(), name='delete_organization'),
    path('delete/my-organization/<int:organization_id>/', DeleteMyOrganizationView.as_view(), name='delete_my_organization'),
    path('get/<int:organization_id>/', GetOrganizationView.as_view(), name='get_organization'),
    path('get-all/', GetOrganizationsView.as_view(), name='get_organizations'),
    path('my-organizations/', GetUserOrganizationsView.as_view(), name='get_user_organizations'),
    path('get-unapproved/', GetUnapprovedOrganizationsView.as_view(), name='get_unapproved_organizations'),
    path('approve/<int:organization_id>/', ApproveOrganizationView.as_view(), name='approve_organization'),
    path('add-moderator/<int:organization_id>/', AddModeratorView.as_view(), name='add_moderator'),
    path('remove-moderator/<int:organization_id>/', RemoveModeratorView.as_view(), name='remove_moderator'),

    # Invitation endpoints
    path('invite-moderator/<int:organization_id>/', CreateModeratorInvitationView.as_view(), name='invite_moderator'),
    path('invitation/<str:token>/', GetInvitationView.as_view(), name='get_invitation'),
    path('invitation/accept/', AcceptInvitationView.as_view(), name='accept_invitation'),
    path('invitation/decline/', DeclineInvitationView.as_view(), name='decline_invitation'),
    path('invitations/sent/<int:organization_id>/', GetSentInvitationsView.as_view(), name='get_sent_invitations'),
    path('invitations/received/', GetReceivedInvitationsView.as_view(), name='get_received_invitations'),
]