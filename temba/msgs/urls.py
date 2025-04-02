from .views import BroadcastCRUDL, LabelCRUDL, MsgCRUDL, ConversationCRUDL

urlpatterns = MsgCRUDL().as_urlpatterns()
urlpatterns += BroadcastCRUDL().as_urlpatterns()
urlpatterns += LabelCRUDL().as_urlpatterns()
urlpatterns += ConversationCRUDL().as_urlpatterns()
