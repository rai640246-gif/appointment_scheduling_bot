from django.urls import path
from .views import register, render_login, user_login, logout_view, dashboard, chat_history, service_list, service_create, service_update , service_delete, select_service, get_appointments

urlpatterns = [
    path('register/', register, name='register'),
    path('', render_login, name='login'),
    path('login/', user_login, name='Login'),
    path('logout/', logout_view, name='logout'),
    path('dashboard/', dashboard, name='dashboard'),
    path('save_chat/', chat_history, name='save_chat'),
    path('list/', service_list, name='service_list'),
    path('create/', service_create, name='service_create'),
    path('update/<int:pk>/', service_update, name='service_update'),
    path('delete/<int:pk>/', service_delete, name='service_delete'),
    path('select-service/', select_service, name='select_service'),
    path('appointments/', get_appointments, name='appointment_list'),
]
