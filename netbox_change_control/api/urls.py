from netbox.api.routers import NetBoxRouter

from . import views

router = NetBoxRouter()
router.register('policies', views.PolicyViewSet)
router.register('policy-rules', views.PolicyRuleViewSet)
router.register('change-requests', views.ChangeRequestViewSet)
router.register('reviews', views.ReviewViewSet)
router.register('checks', views.MergeCheckViewSet)
router.register('change-comments', views.ChangeCommentViewSet)

app_name = 'netbox_change_control-api'
urlpatterns = router.urls
