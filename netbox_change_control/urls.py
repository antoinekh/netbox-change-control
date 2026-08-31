from django.urls import include, path
from utilities.urls import get_model_urls

from . import views

urlpatterns = [
    # Policies
    path('policies/', views.PolicyListView.as_view(), name='policy_list'),
    path('policies/add/', views.PolicyEditView.as_view(), name='policy_add'),
    path('policies/edit/', views.PolicyBulkEditView.as_view(), name='policy_bulk_edit'),
    path('policies/delete/', views.PolicyBulkDeleteView.as_view(), name='policy_bulk_delete'),
    path('policies/<int:pk>/', include(get_model_urls('netbox_change_control', 'policy'))),
    # Policy rules
    path('policy-rules/', views.PolicyRuleListView.as_view(), name='policyrule_list'),
    path('policy-rules/add/', views.PolicyRuleEditView.as_view(), name='policyrule_add'),
    path('policy-rules/edit/', views.PolicyRuleBulkEditView.as_view(), name='policyrule_bulk_edit'),
    path('policy-rules/delete/', views.PolicyRuleBulkDeleteView.as_view(), name='policyrule_bulk_delete'),
    path('policy-rules/<int:pk>/', include(get_model_urls('netbox_change_control', 'policyrule'))),
    # Change requests
    path('change-requests/', views.ChangeRequestListView.as_view(), name='changerequest_list'),
    path('change-requests/add/', views.ChangeRequestEditView.as_view(), name='changerequest_add'),
    path('change-requests/edit/', views.ChangeRequestBulkEditView.as_view(), name='changerequest_bulk_edit'),
    path('change-requests/delete/', views.ChangeRequestBulkDeleteView.as_view(), name='changerequest_bulk_delete'),
    path(
        'change-requests/<int:pk>/submit/',
        views.SubmitForReviewView.as_view(),
        name='changerequest_submit',
    ),
    path(
        'change-requests/<int:pk>/review/',
        views.SubmitReviewView.as_view(),
        name='changerequest_review',
    ),
    path(
        'change-requests/<int:pk>/return-to-draft/',
        views.ReturnToDraftView.as_view(),
        name='changerequest_return_to_draft',
    ),
    path(
        'change-requests/<int:pk>/abandon/',
        views.AbandonChangeRequestView.as_view(),
        name='changerequest_abandon',
    ),
    path(
        'change-requests/<int:pk>/reopen/',
        views.ReopenChangeRequestView.as_view(),
        name='changerequest_reopen',
    ),
    path(
        'change-requests/<int:pk>/run-checks/',
        views.RunChecksView.as_view(),
        name='changerequest_run_checks',
    ),
    path(
        'change-requests/<int:pk>/comment/',
        views.AddChangeCommentView.as_view(),
        name='changerequest_add_comment',
    ),
    path(
        'change-comments/<int:pk>/resolve/',
        views.ResolveChangeCommentView.as_view(),
        name='changecomment_resolve',
    ),
    path('change-comments/<int:pk>/', include(get_model_urls('netbox_change_control', 'changecomment'))),
    path('change-requests/<int:pk>/', include(get_model_urls('netbox_change_control', 'changerequest'))),
    # Merge checks
    path('checks/', views.MergeCheckListView.as_view(), name='mergecheck_list'),
    path('checks/delete/', views.MergeCheckBulkDeleteView.as_view(), name='mergecheck_bulk_delete'),
    path('checks/<int:pk>/', include(get_model_urls('netbox_change_control', 'mergecheck'))),
    # Reviews
    path('reviews/', views.ReviewListView.as_view(), name='review_list'),
    path('reviews/delete/', views.ReviewBulkDeleteView.as_view(), name='review_bulk_delete'),
    path('reviews/<int:pk>/', include(get_model_urls('netbox_change_control', 'review'))),
]
