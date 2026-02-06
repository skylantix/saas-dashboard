from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/reset-password/', views.request_password_reset, name='request_password_reset'),
    path('logout/', views.logout_view, name='logout'),
]
